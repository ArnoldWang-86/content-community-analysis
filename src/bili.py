# -*- coding: utf-8 -*-
"""bili.py —— B站接口客户端（含 wbi 签名 / 重试退避 / 限流）

实测结论(2026-09):
  - search/type 需要 wbi 签名，否则 page 参数被忽略（永远返回第1页）
  - view / relation/stat / dm/list.so 无需签名
  - ranking/v2 / reply/* 风控拦截，不可用
"""
import hashlib, random, time, urllib.parse
import requests

MIXIN_TAB = [46,47,18,2,53,8,23,32,15,50,10,31,58,3,45,35,27,43,5,49,33,9,42,19,
             29,28,14,39,12,38,41,13,37,48,7,16,24,55,40,61,26,17,0,1,60,51,30,4,22,
             25,54,21,56,59,6,63,57,62,11,36,20,34,44,52]
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")


class Bili:
    def __init__(self, sleep=1.2):
        self.sleep = sleep
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA,
                               "Referer": "https://www.bilibili.com/",
                               "Accept": "application/json, text/plain, */*",
                               "Accept-Language": "zh-CN,zh;q=0.9"})
        self._mixin = None
        self._boot()

    def _boot(self):
        try:
            self.s.get("https://www.bilibili.com/", timeout=15)
        except Exception as e:
            print("[warn] 首页失败:", e)
        self._refresh_wbi()

    def _refresh_wbi(self):
        try:
            j = self.s.get("https://api.bilibili.com/x/web-interface/nav", timeout=15).json()
            w = (j.get("data") or {}).get("wbi_img") or {}
            ik = w.get("img_url", "").rsplit("/", 1)[-1].split(".")[0]
            sk = w.get("sub_url", "").rsplit("/", 1)[-1].split(".")[0]
            if len(ik) == 32 and len(sk) == 32:
                self._mixin = "".join((ik + sk)[i] for i in MIXIN_TAB)[:32]
                return True
        except Exception as e:
            print("[warn] wbi 获取失败:", e)
        self._mixin = None
        return False

    def _sign(self, params):
        p = dict(params)
        p["wts"] = int(time.time())
        q = urllib.parse.urlencode(sorted((k, str(v)) for k, v in p.items()))
        p["w_rid"] = hashlib.md5((q + self._mixin).encode()).hexdigest()
        return p

    def get(self, url, params=None, signed=False, tries=4, raw=False, timeout=20):
        params = params or {}
        for t in range(tries):
            try:
                q = self._sign(params) if (signed and self._mixin) else params
                r = self.s.get(url, params=q, timeout=timeout)
                if raw:
                    return r
                j = r.json()
                code = j.get("code")
                if code == 0:
                    return j
                if code in (-352, -412):
                    if signed:
                        self._refresh_wbi()
                    w = 2 ** t + random.random() * 2
                    print(f" [risk {code}] 退避 {w:.1f}s")
                    time.sleep(w)
                    continue
                return j
            except Exception as e:
                w = 2 ** t + random.random() * 2
                print(f" [err {type(e).__name__}] 退避 {w:.1f}s")
                time.sleep(w)
        return {"code": -999, "message": "retries exhausted"}

    def sleep_a_bit(self):
        time.sleep(self.sleep + random.random() * 0.3)

    # ---------- 业务方法 ----------
    def search(self, kw, page, order=None):
        p = {"search_type": "video", "keyword": kw, "page": page}
        if order:
            p["order"] = order
        j = self.get("https://api.bilibili.com/x/web-interface/search/type",
                     p, signed=True)
        return ((j.get("data") or {}).get("result") or []) if j.get("code") == 0 else []

    def video(self, bvid):
        j = self.get("https://api.bilibili.com/x/web-interface/view", {"bvid": bvid})
        return j.get("data") if j.get("code") == 0 else None

    def follower(self, mid):
        j = self.get("https://api.bilibili.com/x/relation/stat", {"vmid": mid})
        return (j.get("data") or {}).get("follower") if j.get("code") == 0 else None

    def danmaku_xml(self, cid, tries=3):
        """获取弹幕 XML

        实测(2026-09): api.bilibili.com/x/v1/dm/list.so 高频请求后会返回 HTTP 412
        （返回 3400 字节的 HTML 错误页而非 XML），导致弹幕被静默采空。
        comment.bilibili.com/{cid}.xml 内容完全相同且稳定，故优先使用。
        """
        sources = [
            (f"https://comment.bilibili.com/{cid}.xml", None),
            ("https://api.bilibili.com/x/v1/dm/list.so", {"oid": cid}),
        ]
        for attempt in range(tries):
            for url, params in sources:
                try:
                    r = self.s.get(url, params=params, timeout=25)
                    if r.status_code == 200 and b"<i>" in r.content:
                        return r.content
                except Exception:
                    pass
                time.sleep(0.4)
            time.sleep(1.0 + attempt)
        return None
