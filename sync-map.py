# -*- coding: utf-8 -*-
"""
ABCmap 词汇地图同步脚本
扫描同目录所有「数字.html」，读取各自的 WORD-META，重建 ABCmap.html。
幂等：同一文件夹连续运行两次结果完全一致。禁止手改 ABCmap.html。
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MAP_NAME = "ABCmap.html"
BASIC_NAME = "基础词表.html"
SERIAL_NAME = "连载.html"
CW_FILE = "compulsory-words.md"

META_RE = re.compile(r"<!--WORD-META\s*(\{.*?\})\s*-->", re.S)
SERIAL_META_RE = re.compile(r"<!--SERIAL-META\s*(\{.*?\})\s*-->", re.S)
TITLE_RE = re.compile(r"<title>(.*?)</title>", re.S)
ORIG_RE = re.compile(r'id="original".*?<div class="prose">(.*?)</div>', re.S)

# 「认识即可」横切标签：认得出来就行，不必会拼会用。只作浏览入口，不参与判定。
EASY_BUCKETS = [
    ("月份", "january february march april may june july august september october november december"),
    ("星期", "monday tuesday wednesday thursday friday saturday sunday "
             "mon tue tues wed thu thur thurs fri sat sun"),
    ("数词", "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
             "sixteen seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety "
             "hundred thousand million billion first second third fourth fifth sixth seventh eighth ninth "
             "tenth eleventh twelfth twentieth once twice"),
    ("不规则变形", "am is are was were be been being ate went did does do has have had made took came got "
                   "said saw knew could would should might must will shall can"),
    ("国名·语言", "africa african asia asian america american antarctica australia australian europe european "
                  "china chinese england english france french germany german japan japanese india indian "
                  "russia russian canada canadian britain british london paris italy italian"),
]


IRREG = {
    "undergone": "undergo", "went": "go", "gone": "go", "sent": "send", "ate": "eat",
    "made": "make", "took": "take", "came": "come", "got": "get", "said": "say",
    "saw": "see", "knew": "know", "did": "do", "does": "do", "had": "have",
    "was": "be", "were": "be", "been": "be", "bought": "buy", "brought": "bring",
    "thought": "think", "found": "find", "left": "leave", "felt": "feel",
    "kept": "keep", "held": "hold", "lost": "lose", "paid": "pay", "ran": "run",
    "sold": "sell", "told": "tell", "wrote": "write", "given": "give", "taken": "take",
    "chosen": "choose", "begun": "begin", "broken": "break", "driven": "drive",
    "eaten": "eat", "fallen": "fall", "forgotten": "forget", "spoken": "speak",
    "stolen": "steal", "worn": "wear", "won": "win", "built": "build",
    "spent": "spend", "grown": "grow", "known": "know", "shown": "show",
    "thrown": "throw", "hidden": "hide", "risen": "rise", "written": "write",
}


def stems(w):
    """粗词干候选集：shape/shaped、attend/attending、differ/differences、undergone/undergo 都要能互相匹配。"""
    w = re.sub(r"[^a-z]", "", w.split(" (")[0].lower())
    w = IRREG.get(w, w)
    out = {w}
    for suf in ("ies", "ing", "ed", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            base = w[:-3] + "y" if suf == "ies" else w[:-len(suf)]
            out.add(base)
            if len(base) >= 3 and base[-1] == base[-2]:
                out.add(base[:-1])
    if w.endswith("ed") and len(w) > 4:
        out.add(w[:-1])
    if w.endswith("ing") and len(w) > 5:
        out.add(w[:-3] + "e")
    return out


STOPW = {"a", "an", "the", "of", "to", "in", "on", "at", "and", "or", "for", "with",
         "by", "be", "is", "are", "was", "were", "as", "it", "its", "that", "this"}


def chunk_tokens(text):
    """语块的内容词：去掉功能词与单字母占位（A/B）。"""
    ws = [x for x in re.findall(r"[a-z][a-z'\-]*", text.split(" (")[0].lower()) if len(x) > 1]
    return [x for x in ws if x not in STOPW] or ws


def check_coverage(meta, dp_text):
    """校验衍生文是否复现了全部 core + guess 条目；多词语块按整块（全部内容词）校验。"""
    if not dp_text:
        return []
    toks = set()
    for t in re.findall(r"[A-Za-z][A-Za-z'\-]*", dp_text):
        toks |= stems(t)
    miss = []
    for w in meta.get("words", []):
        if w["c"] not in ("core", "guess"):
            continue
        if not all(stems(t) & toks for t in chunk_tokens(w["w"])):
            miss.append(w["w"])
    return miss


def load_articles():
    """扫描数字.html，返回 [(n, meta, title, meta_ok, dp_text)]，按序号排序。"""
    items = []
    for fn in os.listdir(HERE):
        m = re.fullmatch(r"(\d+)\.html", fn)
        if not m:
            continue
        path = os.path.join(HERE, fn)
        with open(path, encoding="utf-8") as f:
            html = f.read()
        tm = TITLE_RE.search(html)
        title = re.sub(r"^\d+\s*·\s*", "", (tm.group(1).strip() if tm else fn))
        mm = META_RE.search(html)
        meta, ok = None, False
        if mm:
            try:
                meta = json.loads(mm.group(1))
                ok = (int(meta.get("n", -1)) == int(m.group(1)))
            except (json.JSONDecodeError, ValueError, TypeError):
                ok = False
        dp = " ".join(re.findall(r'<div class="dp">(.*?)</div>', html, re.S))
        items.append((int(m.group(1)), meta, title, ok, dp))
    items.sort(key=lambda x: x[0])
    return items


def aggregate(items):
    """聚合所有条目：word -> {c, m, k, arts:[n...]}。级别 core > guess > junior > cog。"""
    rank = {"core": 4, "guess": 3, "junior": 2, "cog": 1}
    words = {}
    for n, meta, _, ok, _ in items:
        if not (ok and meta):
            continue
        for w in meta.get("words", []):
            key = w["w"].strip()
            if not key:
                continue
            e = words.setdefault(key, {"c": w["c"], "m": w["m"],
                                       "k": w.get("k", ""), "arts": []})
            if rank.get(w["c"], 0) > rank.get(e["c"], 0):
                e["c"] = w["c"]
                e["m"] = w["m"]
                e["k"] = w.get("k", "")
            if n not in e["arts"]:
                e["arts"].append(n)
    return words


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def cat_label(c):
    return {"core": "核心必背", "guess": "可猜词", "junior": "基础词", "cog": "认知词"}.get(c, c)


def difficulty(meta):
    st = (meta or {}).get("stats") or {}
    lem, cor = st.get("lemmas"), st.get("core")
    if not lem or cor is None:
        return ""
    r = cor / lem
    tag = "轻松" if r <= 0.10 else ("适中" if r <= 0.20 else "偏难")
    return f"核心占比 {cor}/{lem}（{round(r*100)}%）· {tag}"


def load_compulsory():
    """读取课标词表，返回 (二级集合, 三级集合)。"""
    p = os.path.join(HERE, CW_FILE)
    if not os.path.exists(p):
        return set(), set()
    t = open(p, encoding="utf-8").read()
    if "## 二级（小学，" not in t:
        return set(), set()
    s2 = t.split("## 二级（小学，")[1].split("## 三级全集")[0]
    s3 = t.split("## 三级全集（初中，")[1]
    g = lambda s: set(x for x in re.findall(r"^[a-z][a-z'\-]*$", s.lower(), re.M) if len(x) > 1)
    return g(s2), g(s3)


def load_seen(items):
    """扫各篇「原文」段实际出现的词，返回 {词干: {篇号}} —— 用于基础词表的「已出现」标记。"""
    seen = {}
    for n, *_ in items:
        p = os.path.join(HERE, f"{n}.html")
        if not os.path.exists(p):
            continue
        with open(p, encoding="utf-8") as f:
            html = f.read()
        m = ORIG_RE.search(html)
        if not m:
            continue
        toks = set()
        for t in re.findall(r"[A-Za-z][A-Za-z'\-]*", m.group(1)):
            toks |= stems(t)
        for t in toks:
            seen.setdefault(t, set()).add(n)
    return seen


def hit_arts(w, seen):
    """某个词出现在哪些篇的原文里。"""
    a = set()
    for s in stems(w):
        a |= seen.get(s, set())
    return sorted(a)


def chips_block(words, seen, pid):
    """按首字母分组渲染词片，返回 (html, 出现的字母列表)。"""
    g = {}
    for w in sorted(words):
        g.setdefault(w[0].upper(), []).append(w)
    out = []
    for k in sorted(g):
        c = []
        for w in g[k]:
            a = hit_arts(w, seen)
            if a:
                t = "、".join(str(x) for x in a)
                c.append(f'<span class="wd hit" title="已出现：第 {t} 篇">{esc(w)}</span>')
            else:
                c.append(f'<span class="wd">{esc(w)}</span>')
        out.append(f'<div class="abrow" id="{pid}-{k}"><b>{k}</b>'
                   f'<span class="chips">{"".join(c)}</span></div>')
    return "".join(out), sorted(g)


def az_strip(letters, pid):
    return "".join(f'<a href="#{pid}-{k}">{k}</a>' for k in letters)


def build_basic_zone():
    """ABCmap 左侧：紧凑状态卡（只报口径 + 两个外链），全表在 基础词表.html。"""
    l2, l3 = load_compulsory()
    if not l3:
        return ('<div class="basicbox"><div class="bl">义务教育基础词</div>'
                '<div class="bs">课标词表缺失</div>'
                '<div class="bn">将 compulsory-words.md 放到本目录</div></div>')
    new = l3 - l2
    return f"""<div class="basicbox">
<div class="bl">义务教育基础词</div>
<div class="bs">课标 2022 · 判定基底</div>
<div class="bn"><b>{len(l3)}</b> 词 · 全集<br>
<span class="bsub"><b>{len(l2)}</b> · 二级（小学）</span><br>
<span class="bsub"><b>{len(new)}</b> · 初中新增</span></div>
<div class="bd">命中即默认已掌握：<br>二级 → 不标注<br>初中新增 → 进「基础词自查」</div>
<div class="blinks">
<a class="blink" href="{BASIC_NAME}#sec2">浏览二级（小学）· {len(l2)} ↗</a>
<a class="blink" href="{BASIC_NAME}#sec3">浏览初中新增 · {len(new)} ↗</a>
</div>
</div>"""


BASIC_CSS = """
:root{--ink:#2C2C2A;--ink2:#5F5E5A;--ink3:#8A857C;--line:#E4DCC8;--line2:#D5C9AE;
  --bg:#FBFAF7;--card:#FFF;--panel:#F5F1E8;--r:10px;--link:#854F0B;
  --amber:#BA7517;--chip:#F2EEE4;--hit:#F7E3B8}
*{margin:0;padding:0;box-sizing:border-box}
html{scroll-behavior:smooth}
body{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;color:var(--ink);
  background:var(--bg);font-size:16px;line-height:1.85;-webkit-font-smoothing:antialiased}
a{color:var(--link);text-decoration:none}
.nav{position:sticky;top:0;z-index:40;background:rgba(251,250,247,.92);
  backdrop-filter:blur(12px);border-bottom:1px solid var(--line)}
.nav-in{max-width:1000px;margin:0 auto;padding:0 28px;height:60px;display:flex;align-items:center;gap:20px}
.brand{font-family:Georgia,serif;font-weight:600;font-size:17px;letter-spacing:1px}
.nav-r{margin-left:auto;font-size:12.5px;color:var(--ink3);display:flex;gap:16px;flex-wrap:wrap}
.wrap{max-width:1000px;margin:0 auto;padding:0 28px}
.hero{padding:56px 0 32px;border-bottom:1px solid var(--line);margin-bottom:22px}
.kicker{font-size:11.5px;letter-spacing:4px;color:var(--ink3)}
.hero h1{font-family:Georgia,serif;font-weight:600;font-size:28px;letter-spacing:1px;margin-top:12px}
.hero .sub{color:var(--ink2);font-size:14.5px;margin-top:12px;max-width:640px}
.bar{display:flex;gap:14px;align-items:center;margin:16px 0 4px;flex-wrap:wrap}
.search{flex:1;min-width:220px;border:1px solid var(--line);border-radius:var(--r);padding:9px 14px;
  font-size:14px;font-family:inherit;color:var(--ink);background:#fff}
.search:focus{outline:none;border-color:var(--line2)}
.tgl{font-size:13px;color:var(--ink2);display:flex;align-items:center;gap:6px;cursor:pointer;user-select:none}
details.easy{border:1px solid var(--line);border-radius:var(--r);padding:12px 16px;margin:14px 0 4px;background:#fff}
details.easy summary{font-size:14.5px;color:var(--ink2);cursor:pointer}
.eg{font-size:12.5px;color:var(--ink2);padding:6px 0;line-height:1.9;border-bottom:1px solid #EDE7DA}
.eg:last-child{border-bottom:none}
.eg b{font-family:Georgia,serif;color:var(--ink);margin-right:8px;font-weight:600}
h2.sec{font-family:Georgia,serif;font-size:19px;font-weight:600;padding-bottom:10px;
  border-bottom:2px solid var(--amber);margin:40px 0 10px;display:flex;align-items:baseline;
  gap:12px;flex-wrap:wrap;scroll-margin-top:80px}
h2.sec em{font-family:inherit;font-size:12.5px;font-style:normal;color:var(--ink3);font-weight:400}
.az{display:flex;flex-wrap:wrap;gap:2px;margin-bottom:12px}
.az a{font-family:Georgia,serif;font-size:12.5px;color:var(--ink3);padding:1px 5px;border-radius:5px;cursor:pointer}
.az a:hover{background:var(--panel);color:var(--ink)}
.abrow{display:flex;gap:12px;padding:5px 0;border-bottom:1px solid #EDE7DA;scroll-margin-top:80px}
.abrow>b{font-family:Georgia,serif;font-size:13.5px;color:var(--ink3);width:16px;flex:none;line-height:1.9}
.chips{display:flex;flex-wrap:wrap;gap:4px}
.wd{font-size:13.5px;color:var(--ink2);background:var(--chip);border-radius:5px;
  padding:2px 9px;line-height:1.7;min-width:2.9em;text-align:center}
.wd.hit{background:var(--hit);color:var(--ink);cursor:help}
.footer{text-align:center;color:var(--ink3);font-size:12px;padding:40px 0 48px;
  border-top:1px solid var(--line);margin-top:44px}
@media(max-width:820px){.abrow{gap:8px}}
"""

BASIC_JS = """
var q=document.getElementById('q'), only=document.getElementById('onlygone');
var bTimer=null;
function applyBasic(){
  var v=(q?q.value:'').trim().toLowerCase(), hide=only?only.checked:false;
  var rows=document.querySelectorAll('.abrow');
  for(var r=0;r<rows.length;r++){
    var ws=rows[r].getElementsByClassName('wd'), n=0;
    for(var i=0;i<ws.length;i++){
      var el=ws[i];
      var ok=(v===''||el.textContent.toLowerCase().indexOf(v)>-1)
             &&(!hide||el.className.indexOf('hit')<0);
      el.style.display=ok?'':'none';
      if(ok){ n++; }
    }
    rows[r].style.display=n?'':'none';
  }
}
function basicDebounced(){ clearTimeout(bTimer); bTimer=setTimeout(applyBasic,120); }
if(q){q.addEventListener('input',basicDebounced);}
if(only){only.addEventListener('change',applyBasic);}
"""


def build_basic_doc(items, seen):
    """生成 基础词表.html：二级 / 初中新增两区 + A–Z 跳转 + 搜索 + 认识即可折叠区 + 已出现标记。"""
    l2, l3 = load_compulsory()
    if not l3:
        return
    new = l3 - l2
    hit2 = sum(1 for w in l2 if hit_arts(w, seen))
    hit3 = sum(1 for w in new if hit_arts(w, seen))

    s2_html, s2_letters = chips_block(l2, seen, "s2")
    s3_html, s3_letters = chips_block(new, seen, "s3")

    easy_total = set()
    easy_rows = []
    for name, ws in EASY_BUCKETS:
        sel = sorted(set(ws.split()) & l3)
        easy_total |= set(sel)
        easy_rows.append(f'<div class="eg"><b>{name} · {len(sel)}</b>'
                         f'<span>{" ".join(esc(x) for x in sel)}</span></div>')

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>基础词表 · 课标 2022</title>
<style>{BASIC_CSS}</style>
</head>
<body>
<div class="nav"><div class="nav-in">
  <span class="brand">基础词表</span>
  <span class="nav-r"><span>{len(l3)} 词</span><span>二级 {len(l2)}</span>
  <span>初中新增 {len(new)}</span><span>已出现 {hit2 + hit3}</span></span>
</div></div>

<div class="wrap">
<div class="hero">
  <div class="kicker">COMPULSORY WORD LIST</div>
  <h1>义务教育基础词 · 课标 2022</h1>
  <div class="sub">三层口径：全集 {len(l3)} ＝ 二级（小学）{len(l2)} ＋ 初中新增 {len(new)}。
  二级命中即默认已掌握、不标注；初中新增命中后进「基础词自查」。</div>
</div>

<div class="bar">
  <input class="search" id="q" placeholder="搜索单词…">
  <label class="tgl"><input type="checkbox" id="onlygone"> 仅看未出现</label>
</div>

<details class="easy">
  <summary>认识即可 · {len(easy_total)} 词（月份 / 星期 / 数词 / 不规则变形 / 国名·语言）</summary>
  {''.join(easy_rows)}
</details>

<h2 class="sec" id="sec2">二级（小学）<em>{len(l2)} 词 · 已出现 {hit2} · 不标注</em></h2>
<div class="az">{az_strip(s2_letters, "s2")}</div>
{s2_html}

<h2 class="sec" id="sec3">初中新增<em>{len(new)} 词 · 已出现 {hit3} · 进「基础词自查」</em></h2>
<div class="az">{az_strip(s3_letters, "s3")}</div>
{s3_html}

<div class="footer">基础词表由 sync-map.py 随每篇阅读自动重建 · 浅黄底＝已在你的原文里出现过</div>
</div>
<script>{BASIC_JS}</script>
</body>
</html>
"""
    with open(os.path.join(HERE, BASIC_NAME), "w", encoding="utf-8") as f:
        f.write(html)


CSS = """
:root{--ink:#2C2C2A;--ink2:#5F5E5A;--ink3:#8A857C;--line:#E4DCC8;--line2:#D5C9AE;
  --bg:#FBFAF7;--card:#FFF;--panel:#F5F1E8;--r:10px;--amber:#BA7517;--link:#854F0B;
  --up:#A8441C;--rep:#854F0B;--due:#5F7A2A}
*{margin:0;padding:0;box-sizing:border-box}
html{scroll-behavior:smooth}
body{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;color:var(--ink);
  background:var(--bg);font-size:16px;line-height:1.85;-webkit-font-smoothing:antialiased}
a{color:var(--link);text-decoration:none}
.nav{position:sticky;top:0;z-index:40;background:rgba(251,250,247,.92);
  backdrop-filter:blur(12px);border-bottom:1px solid var(--line)}
.nav-in{max-width:1080px;margin:0 auto;padding:0 28px;height:60px;display:flex;align-items:center;gap:20px}
.brand{font-family:Georgia,serif;font-weight:600;font-size:17px;letter-spacing:1px}
.nav-r{margin-left:auto;font-size:12.5px;color:var(--ink3);display:flex;gap:16px}
.wrap{max-width:1080px;margin:0 auto;padding:0 28px}
.hero{padding:64px 0 40px;border-bottom:1px solid var(--line);margin-bottom:36px}
.kicker{font-size:11.5px;letter-spacing:4px;color:var(--ink3)}
.hero h1{font-family:Georgia,serif;font-weight:600;font-size:30px;letter-spacing:1px;margin-top:12px}
.hero .sub{color:var(--ink2);font-size:15px;margin-top:12px;max-width:560px}
.quick{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 16px;margin:-18px 0 32px;
  padding:13px 18px;border:1px solid var(--line);border-radius:var(--r);background:var(--card)}
.quick b{font-size:12px;letter-spacing:2px;color:var(--ink3);font-weight:600;flex:none}
.quick a{font-size:14.5px;color:var(--link)}
.quick a:hover{text-decoration:underline}
.grid{display:grid;grid-template-columns:210px minmax(0,1fr);gap:44px;padding-bottom:56px}
.side{position:sticky;top:84px;align-self:start}
.basicbox{border:1px solid var(--line);border-radius:var(--r);padding:16px 16px 14px;background:var(--card)}
.bl{font-size:14.5px;font-weight:600}
.bs{font-size:12px;color:var(--ink3);margin-top:2px}
.bn{font-size:13px;color:var(--ink2);margin-top:10px;padding-top:10px;border-top:1px solid var(--line);line-height:1.7}
.bd{font-size:12px;color:var(--ink3);margin-top:8px;line-height:1.7}
.bsub{color:var(--ink2)}
.blinks{border-top:1px solid var(--line);margin-top:10px;padding-top:4px}
.blink{display:block;font-size:12.5px;color:var(--ink2);padding:3px 0}
.blink:hover{color:var(--link)}
.side h4{font-size:11px;letter-spacing:2px;color:var(--ink3);margin:22px 0 8px;font-weight:600}
.side .idx{font-size:12.5px;color:var(--ink2);line-height:2;word-spacing:4px}
h2.sec{font-family:Georgia,serif;font-size:19px;font-weight:600;padding-bottom:10px;
  border-bottom:2px solid var(--amber);margin-bottom:6px;display:flex;align-items:baseline;gap:12px}
h2.sec em{font-family:inherit;font-size:12.5px;font-style:normal;color:var(--ink3);font-weight:400}
.search{width:100%;border:1px solid var(--line);border-radius:var(--r);padding:9px 14px;
  font-size:14px;font-family:inherit;color:var(--ink);margin:16px 0 6px;background:#fff}
.search:focus{outline:none;border-color:var(--line2)}
details.grp{border:1px solid var(--line);border-radius:var(--r);padding:12px 16px;margin:10px 0;background:var(--card)}
details.grp summary{font-size:15.5px;font-weight:500;cursor:pointer}
table.ledger{width:100%;border-collapse:collapse;margin-top:10px;font-size:14.5px}
table.ledger th{text-align:left;font-weight:600;color:var(--ink);padding:9px 12px;border-bottom:1px solid var(--line2)}
table.ledger td{padding:9px 12px;border-bottom:1px solid var(--line);color:var(--ink2);vertical-align:top}
table.ledger tr:hover td{background:var(--panel)}
td.en{font-family:Georgia,serif;color:var(--ink)}
.tagk{display:inline-block;font-size:12px;line-height:1.6;color:var(--link);
  border:1px solid var(--line);background:var(--panel);border-radius:4px;
  padding:1px 7px;margin-left:6px;white-space:nowrap;vertical-align:1px}
.up{color:var(--up);font-size:12px}
.rep{color:var(--rep);font-size:12px}
.due{color:var(--due);font-size:12px}
.arts{white-space:nowrap}
.arts a{color:var(--ink2);border-bottom:1px solid var(--line2);margin-right:5px}
.arts a:hover{color:var(--link);border-color:var(--amber)}
.els{color:var(--ink3);margin-right:5px}
.artzone{border-top:1px solid var(--line);padding:22px 0 60px}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:10px;margin-top:12px}
.grp-h{font-size:12px;letter-spacing:2px;color:var(--ink3);margin:22px 0 0;font-weight:600}
.grp-h:first-child{margin-top:14px}
.empty{font-size:13px;color:var(--ink3);padding:8px 0;border:1px dashed var(--line);border-radius:var(--r);text-align:center;margin-top:12px}
.card{border:1px solid var(--line);border-radius:var(--r);padding:12px 14px;background:var(--card)}
.card.new{border:2px solid var(--amber)}
.card b{font-family:Georgia,serif;font-weight:400}
.card .meta{font-size:12.5px;color:var(--ink3);margin-top:4px;line-height:1.6}
.footer{text-align:center;color:var(--ink3);font-size:12px;padding:28px 0 40px;border-top:1px solid var(--line)}
@media(max-width:820px){.grid{grid-template-columns:1fr;gap:24px}.side{position:static}}
"""

JS = """
var q=document.getElementById('q');
if(q){
  var groups=[], timer=null;
  function build(){
    groups=[];
    var gs=document.querySelectorAll('details.grp');
    for(var i=0;i<gs.length;i++){
      var trs=gs[i].querySelectorAll('table.ledger tbody tr'), list=[];
      for(var j=0;j<trs.length;j++){ list.push([trs[j].textContent.toLowerCase(), trs[j]]); }
      groups.push({el:gs[i], rows:list});
    }
  }
  function run(){
    var v=q.value.trim().toLowerCase();
    if(!groups.length){ build(); }
    for(var i=0;i<groups.length;i++){
      var g=groups[i], vis=0;
      for(var j=0;j<g.rows.length;j++){
        var hit=(v==='')||(g.rows[j][0].indexOf(v)>-1);
        g.rows[j][1].style.display=hit?'':'none';
        if(hit){ vis++; }
      }
      g.el.open = (v!=='') && vis>0;
    }
  }
  q.addEventListener('input',function(){ clearTimeout(timer); timer=setTimeout(run,120); });
}
"""


def build_map(items, words):
    arts = list(items)
    n_arts, n_words = len(arts), len(words)
    n_ck = sum(1 for e in words.values() if e.get("k"))
    ups = [w for w, e in words.items()
           if (e["c"] == "cog" and len(e["arts"]) >= 2) or (e["c"] == "junior" and len(e["arts"]) >= 3)]
    last_n = arts[-1][0] if arts else 0
    due = [w for w, e in words.items() if e["c"] == "core" and last_n - max(e["arts"]) >= 3]

    def card(n, meta, title, ok, dp):
        cls = "card new" if n == last_n else "card"
        if ok and meta:
            ws = meta.get("words", [])
            nc = sum(1 for w in ws if w["c"] == "core")
            ng = sum(1 for w in ws if w["c"] == "guess")
            nj = sum(1 for w in ws if w["c"] == "junior")
            no = sum(1 for w in ws if w["c"] == "cog")
            dif = difficulty(meta)
            wstat = f"核心 {nc} · 可猜 {ng} · 基础 {nj} · 认知 {no}" + (f"<br>{dif}" if dif else "")
        else:
            wstat = "⚠️ 缺 WORD-META"
        return (f'<div class="{cls}"><b><a href="{n}.html">{esc(title)}</a></b>'
                f'<div class="meta">第 {n} 篇<br>{wstat}</div></div>')

    art_cards = "".join(card(*a) for a in arts)
    if arts:
        art_cards += (f'<div class="card" style="opacity:.45;"><b>{last_n+1}.html</b>'
                      f'<div class="meta">占位 · 下一篇制作后自动替换</div></div>')
    n_art = len(arts)
    EMPTY = '<div class="empty">尚未开始</div>'

    # 连载：一个设定一个文件（连载*.html），纯复习、不计入账本（不参与 words 聚合）
    ser_html, n_ser, ser_list = "", 0, []
    for fn in sorted(f for f in os.listdir(HERE)
                     if f.startswith("连载") and f.endswith(".html")):
        sm = SERIAL_META_RE.search(open(os.path.join(HERE, fn), encoding="utf-8").read())
        sd = {}
        if sm:
            try:
                sd = json.loads(sm.group(1))
            except json.JSONDecodeError:
                sd = {}
        log = sd.get("log") or []
        eps = sd.get("ep") or len(log)
        n_ser += eps
        season = sd.get("season") or 1
        st = sd.get("title") or fn[:-5]
        lastd = sd.get("last") or (log[-1].get("date", "") if log else "")
        kn = sd.get("kind") or "纯复习 · 不计入账本"
        ser_list.append((fn, st, eps))
        ser_html += (f'<div class="card"><b><a href="{fn}#ep{eps}">{esc(st)}</a></b>'
                     f'<div class="meta">第 {season} 季 · 共 {eps} 集'
                     + (f' · 最近 {esc(lastd)}' if lastd else "")
                     + f'<br>{esc(kn)} ｜ <a href="{fn}">看全部</a></div></div>')
    ser_html = ser_html or EMPTY

    # 「继续读」入口条：一次点击开始，不用滚到最底、不用展开
    qi = []
    if arts:
        qi.append(f'<a href="{arts[-1][0]}.html">{esc(arts[-1][2])}</a>')
    for fn, st, eps in ser_list:
        qi.append(f'<a href="{fn}#ep{eps}">{esc(st)} · 第 {eps} 集</a>')
    qi.append(f'<a href="{BASIC_NAME}">基础词全表</a>')
    quick = ('<div class="quick"><b>继续读</b>' + "".join(qi) + '</div>') if arts else ""

    KIND = {"collocation": "搭配", "phrase": "句式框架", "phrasal": "短语动词", "discourse": "语篇标记"}

    def row(w, e):
        a = e["arts"]
        shown = a if len(a) <= 3 else [a[0], None, a[-2], a[-1]]
        parts = []
        for x in shown:
            parts.append('<span class="els">…</span>' if x is None
                         else f'<a href="{x}.html">{x}</a>')
        links = " ".join(parts)
        full = "、".join(str(x) for x in a)
        ktag = KIND.get(e.get("k") or "", "")
        khtml = f'<span class="tagk">{ktag}</span>' if ktag else ""
        flags = []
        if e["c"] == "cog" and len(e["arts"]) >= 2:
            flags.append('<span class="up">建议升为必背</span>')
        if e["c"] == "junior" and len(e["arts"]) >= 3:
            flags.append('<span class="up">反复出现 · 建议升为必背</span>')
        if e["c"] == "core" and len(e["arts"]) >= 2:
            flags.append('<span class="rep">旧词重现 · 自查即可</span>')
        if e["c"] == "core" and last_n - max(e["arts"]) >= 3:
            flags.append('<span class="due">待复现</span>')
        return (f'<tr><td class="en">{esc(w)}{khtml}</td><td>{esc(e["m"])}</td>'
                f'<td class="arts" title="出现在：第 {full} 篇">{links}</td>'
                f'<td>{len(a)}</td>'
                f'<td>{" ".join(flags)}</td></tr>')

    head = ('<tr><th style="width:24%">条目</th><th style="width:24%">首现义</th>'
            '<th style="width:12%">出现</th><th style="width:7%">篇数</th><th>提示</th></tr>')
    groups = []
    for c, label in (("core", "核心必背"), ("guess", "可猜词"), ("junior", "基础词"), ("cog", "认知词")):
        sub = {w: e for w, e in words.items() if e["c"] == c}
        if not sub:
            continue
        nk = sum(1 for e in sub.values() if e.get("k"))
        lab = f"{label}（{len(sub)}）" + (f" · 含语块 {nk}" if nk else "")
        body = "".join(row(w, sub[w]) for w in sorted(sub, key=lambda k: (-len(sub[k]["arts"]), k.lower())))
        groups.append(f'<details class="grp">'
                      f'<summary>{lab}</summary>'
                      f'<table class="ledger">{head}{body}</table></details>')
    ledger = "".join(groups) or '<p style="color:var(--ink3);font-size:14px">还没有文章。</p>'

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ABCmap · 词汇地图</title>
<style>{CSS}</style>
</head>
<body>
<div class="nav"><div class="nav-in">
  <span class="brand">ABCmap</span>
  <span class="nav-r"><span>{n_arts} 篇</span><span>{n_words} 条</span>
  <span>语块 {n_ck}</span><span>升格 {len(ups)}</span><span>待复现 {len(due)}</span></span>
</div></div>

<div class="wrap">
<div class="hero">
  <div class="kicker">VOCABULARY MAP</div>
  <h1>词汇地图</h1>
  <div class="sub">左侧是地基，右侧是积累。每做完一篇阅读或一集连载，重新运行「同步ABCmap.cmd」。</div>
</div>
{quick}

<div class="grid">
  <aside class="side">
    {build_basic_zone()}
    <h4>说明</h4>
    <div class="idx">基础词跨 ≥3 篇 → 建议升为必背<br>认知词跨 ≥2 篇 → 建议升为必背<br>核心词 ≥3 篇未现 → 待复现</div>
  </aside>

  <main>
    <h2 class="sec">长期账本 <em>{n_words} 词 · 按跨篇复现数排序</em></h2>
    <input class="search" id="q" placeholder="搜索单词或释义…">
    {ledger}
  </main>
</div>

<div class="artzone">
  <h2 class="sec">文章 <em>{n_arts} 篇{(" · 连载 " + str(n_ser) + " 集") if n_ser else ""}</em></h2>
  <details><summary style="cursor:pointer;font-size:15.5px;color:var(--ink2);margin-top:12px">展开文章列表</summary>
  <div class="grp-h">连载 · {n_ser} 集</div>
  <div class="cards">{ser_html}</div>
  <div class="grp-h">文章 · {n_art} 篇</div>
  <div class="cards">{art_cards or EMPTY}</div>
  </details>
</div>

<div class="footer">ABCmap 由 sync-map.py 重建 · 幂等 · 死链免疫（每次按真实文件重建）</div>
</div>
<script>{JS}</script>
</body>
</html>
"""
    with open(os.path.join(HERE, MAP_NAME), "w", encoding="utf-8") as f:
        f.write(html)


def report(items, words):
    print("=" * 46)
    print("ABCmap 同步报告")
    print("=" * 46)
    nums = [n for n, *_ in items]
    print(f"文章：{len(items)} 篇 ｜ 收录词汇：{len(words)} 个")
    warns = []
    for n, meta, title, ok, dp in items:
        if not meta:
            warns.append(f"⚠️ {n}.html（{title}）缺 WORD-META，已降级（仅提取标题）")
            continue
        if not ok:
            warns.append(f"⚠️ {n}.html 的 WORD-META 中 n 与文件名不一致")
        miss = check_coverage(meta, dp)
        if miss:
            warns.append(f"⚠️ {n}.html 衍生文未复现 {len(miss)} 个目标词：" + "、".join(miss[:12]))
        if not (meta.get("stats") or {}).get("lemmas"):
            warns.append(f"ℹ️ {n}.html 缺 stats（词型数），难度标签不显示")
    if nums and nums != list(range(1, nums[-1] + 1)):
        warns.append(f"⚠️ 编号断档：现有 {nums}")
    stray = [f for f in os.listdir(HERE) if f.endswith(".html")
             and not re.fullmatch(r"\d+\.html", f)
             and f not in (MAP_NAME, BASIC_NAME) and not f.startswith("连载")]
    if stray:
        warns.append("ℹ️ 未纳入地图的零散 html：" + "、".join(stray))
    for fn in sorted(f for f in os.listdir(HERE)
                     if f.startswith("连载") and f.endswith(".html")):
        sm = SERIAL_META_RE.search(open(os.path.join(HERE, fn), encoding="utf-8").read())
        if not sm:
            warns.append(f"⚠️ {fn} 缺 SERIAL-META，地图卡片会缺标题/集数")
            continue
        try:
            sd = json.loads(sm.group(1))
        except json.JSONDecodeError:
            warns.append(f"⚠️ {fn} 的 SERIAL-META JSON 不合法")
            continue
        eps = sd.get("ep") or len(sd.get("log") or [])
        if eps >= 12:
            warns.append(f"ℹ️ {fn} 已 {eps} 集，建议另起一季（…-第2季.html）"
                         "——单文件过大后打开会变慢")
    for w in warns:
        print(w)
    if not warns:
        print("✓ 无警告：META 完整、编号连续、衍生文复现全覆盖")
    print(f"✓ {MAP_NAME} 已重建")
    print(f"✓ {BASIC_NAME} 已重建")
    return warns


def main():
    items = load_articles()
    words = aggregate(items)
    seen = load_seen(items)
    build_map(items, words)
    build_basic_doc(items, seen)
    warns = report(items, words)
    if "--out" in sys.argv:
        p = sys.argv[sys.argv.index("--out") + 1]
        with open(p, "w", encoding="utf-8") as f:
            f.write("\n".join(warns) if warns else "NO_WARNING")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
