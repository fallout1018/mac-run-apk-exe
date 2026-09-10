#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
collect_issues.py —— 从 GitHub Issues 生成 COMPAT.md，并同步更新 README 首页预览。
用法：python3 scripts/collect_issues.py
依赖：gh CLI（已 gh auth login）
"""
import os
import re
import sys
import subprocess
import json
from datetime import datetime

# ============ 配置 ============
REPO = "fallout1018/mac-run-apk-exe"
OUT_FILE = "COMPAT.md"
README_FILE = "README.md"
FILTER_BY_LABEL = False      # False = 靠标题 [兼容] 过滤（Issue 未打标签时用）
LABEL = "兼容"
PREVIEW_LIMIT = 5            # README 首页显示几条「完美运行」，改 0 = 只留跳转链接
# =================================


def run(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True)


def gh_json(query):
    r = run(f"gh api {query}")
    if r.returncode != 0 or not r.stdout.strip():
        print(f"⚠️ gh api 失败: {query}\n{r.stderr}")
        return []
    return json.loads(r.stdout)


def clean(v):
    """去 emoji、首尾空白。"""
    return re.sub(r"[✅🔶❌]", "", (v or "").strip()).strip()


def is_heading(text):
    """字段标题行：含中文 + 含 ｜ 或英文冒号（不要求 ** 包裹，兼容纯文本表单渲染）。"""
    t = text.strip()
    if not t:
        return False
    has_cn = bool(re.search(r"[\u4e00-\u9fff]", t))
    has_sep = ("｜" in t) or ("|" in t) or bool(re.search(r"[A-Za-z]:\s*$", t))
    return has_cn and has_sep


def parse_body(body):
    """解析 Issue 正文，返回 {中文键: 值}。
    兼容两种渲染：
      - 双行：「### 芯片系列 ｜ Chip Family\nM1」
      - 单行：「运行状态 ｜ ✅完美运行 ｜ Works perfectly」
    """
    fields = {}
    if not body:
        return fields
    lines = body.splitlines()

    for i, raw in enumerate(lines):
        line = raw.strip()
        if not is_heading(line):
            continue

        # ---- 取值 ----
        val = ""
        m = re.match(r"^.*?[｜|]\s*(.+)$", line)
        after = m.group(1).strip() if m else ""
        # after 是"值"还是"英文标签"？含中文/数字视为值
        looks_like_value = bool(re.search(r"[\u4e00-\u9fff0-9]", after)) or \
                           bool(re.match(r"^\s*no response", after, re.I))
        if m and looks_like_value:
            val = after           # 同行值（运行状态 等）
        else:
            for j in range(i + 1, len(lines)):   # 下行值（芯片/内存 等）
                if lines[j].strip():
                    val = lines[j].strip()
                    break
        val = clean(val)
        if val.lower() in ("no response",):
            val = ""

        # ---- 取 key（｜ 前的中文部分，去 ###）----
        key = re.sub(r"^#{1,6}\s*", "", line)
        key = re.sub(r"\s*[｜|].*$", "", key).strip()
        if key:
            fields[key] = val

    # ---- 后处理：「性能 / 画质 / 帧率（可选）」这类【无 ｜ 分隔】的字段 ----
    joined = "\n".join(lines)
    pm = re.search(r"性能\s*/\s*画质\s*/\s*帧率[^：:\n]*[：:]\s*(.+?)(?:\n|$)", joined)
    perf = clean(pm.group(1)) if pm else ""
    if not perf or perf.lower() == "no response":
        for i, raw in enumerate(lines):
            if re.match(r"^#{0,6}\s*性能\s*/", raw) or raw.strip().startswith("性能 / 画质"):
                for j in range(i + 1, len(lines)):
                    if lines[j].strip():
                        perf = clean(lines[j])
                        break
                break
    if perf and perf.lower() != "no response":
        fields.setdefault("性能 / 画质 / 帧率", perf)

    return fields


def g(fields, *keys):
    """按中文片段模糊取字段值。"""
    for k in keys:
        for fk, fv in fields.items():
            if k in fk and fv:
                return fv
    return ""


def classify_status(text):
    t = clean(text).lower()
    if "瑕疵" in t or "partial" in t:
        return "partial"
    if "跑不了" in t or "broken" in t:
        return "broken"
    return "working"


def fetch_issues():
    print("📦 正在从 GitHub 拉取 Issues...")
    fields = "number,title,body,state,url,author,labels,createdAt"
    if FILTER_BY_LABEL:
        issues = gh_json(f"repos/{REPO}/issues?state=all&per_page=100&labels={LABEL}")
    else:
        all_issues = gh_json(f"repos/{REPO}/issues?state=open&per_page=100")
        issues = [i for i in all_issues if "兼容" in i.get("title", "")]
    return [i for i in issues if not i.get("pull_request")]


def build_entry(issue):
    f = parse_body(issue.get("body", ""))

    # 标题兜底（正文缺软件名时）
    title = issue.get("title", "")
    name = g(f, "软件名称", "App") or _name_from_title(title)
    status_raw = g(f, "运行状态", "状态") or title

    chip_family = g(f, "芯片系列", "Chip") or "?"
    chip_variant = g(f, "芯片后缀", "Variant") or ""
    chip = chip_family
    if chip_variant and chip_variant != "标准版":
        chip = f"{chip_family} / {chip_variant}"

    memory = g(f, "内存", "Memory") or "?"
    model = g(f, "机型", "Model") or "?"

    # 备注：优先「性能/画质/帧率」，其次「补充说明」
    notes = g(f, "性能 / 画质 / 帧率", "性能", "帧率") or g(f, "补充说明") or "-"
    notes = notes.replace("\n", " ")[:80]

    author = (issue.get("author") or issue.get("user") or {}).get("login", "?")

    return {
        "name": name or "?",
        "type": g(f, "软件类型", "类型", "Type") or "APK/安卓应用",
        "chip": chip,
        "memory": memory,
        "model": model,
        "yyb": g(f, "应用宝版本", "YYB", "Version") or "",
        "status": classify_status(status_raw),
        "notes": notes,
        "contributor": f"@{author}",
        "url": issue.get("url", ""),
    }


def _name_from_title(title):
    m = re.search(r"\[\s*兼容\s*\]\s*(.+?)(?:\s*[-—]\s*|\s*$)", title)
    if m:
        return m.group(1).strip()
    return title.strip()


def md_row(r):
    yyb = r["yyb"] or "-"
    return (f"| {r['name']} | {r['type']} | {r['chip']} | {r['memory']} | "
            f"{r['model']} | {yyb} | {r['notes']} | {r['contributor']} |")


TABLE_HEADER = (
    "| 软件 | 类型 | 芯片 | 内存 | 机型 | 应用宝版本 | 备注 | 贡献者 |\n"
    "|------|------|------|------|------|------------|------|----------|\n"
)


def render_table(rows):
    if not rows:
        return ""   # 空分类不渲染表格，避免占位
    return TABLE_HEADER + "".join(md_row(r) + "\n" for r in rows)


def main():
    issues = fetch_issues()
    print(f"共获取到 {len(issues)} 条 Issues")

    working, partial, broken = [], [], []
    for iss in issues:
        entry = build_entry(iss)
        {"partial": partial, "broken": broken}.get(entry["status"], working).append(entry)
    for lst in (working, partial, broken):
        lst.sort(key=lambda x: (x["model"], x["name"]))

    out = [
        "# 兼容清单 ｜ Compatibility List",
        "",
        f"> 更新于 ｜ Updated: {datetime.now().strftime('%Y-%m-%d')}　数据来源 ｜ Source: GitHub Community Issues",
        "",
        "## ✅ 完美运行 ｜ Working",
        render_table(working) or "（暂无 ｜ Empty）",
        "",
        "## 🔶 能跑但有瑕疵 ｜ Partial",
        render_table(partial) or "（暂无 ｜ Empty）",
        "",
        "## ❌ 跑不了 ｜ Broken",
        render_table(broken) or "（暂无 ｜ Empty）",
        "",
        "---",
        "*此文件由 `collect_issues.py` 自动生成，请勿手动编辑 ｜ Auto-generated, do not edit manually.*",
        "",
    ]
    with open(OUT_FILE, "w", encoding="utf-8") as fp:
        fp.write("\n".join(out))
    print(f"✅ 已生成 {OUT_FILE}：✅{len(working)} ｜ 🔶{len(partial)} ｜ ❌{len(broken)}")

    update_readme_preview(working)


def update_readme_preview(working):
    """把「完美运行」前 N 条注入 README 的 COMPAT_PREVIEW 标记区间。"""
    if not os.path.exists(README_FILE):
        return
    rows = [md_row(r) for r in working[:PREVIEW_LIMIT]]
    preview = ""
    if rows:
        preview = "#### ✅ 完美运行（预览）｜ Working (preview)\n\n" + TABLE_HEADER + "".join(rows)
    else:
        preview = "_暂无数据，提交兼容测试 Issue 后会自动出现 ｜ No data yet._"

    block = (
        "<!-- COMPAT_PREVIEW:start -->\n"
        f"{preview}\n"
        "<!-- COMPAT_PREVIEW:end -->"
    )
    with open(README_FILE, "r", encoding="utf-8") as fp:
        content = fp.read()
    new_content, n = re.subn(
        r"<!-- COMPAT_PREVIEW:start -->.*?<!-- COMPAT_PREVIEW:end -->",
        block, content, flags=re.S,
    )
    if n:
        with open(README_FILE, "w", encoding="utf-8") as fp:
            fp.write(new_content)
        print(f"✅ 已更新 README.md 兼容预览（{len(rows)} 条）")


if __name__ == "__main__":
    main()
