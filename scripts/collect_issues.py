#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
collect_issues.py - 从 GitHub Issues 汇总兼容报告到 COMPAT.md
用法：python3 scripts/collect_issues.py

v2：
- 直接用 `gh issue list --json` 拉取（避免手写 REST URL 拼错）
- 兼容「字段名 ｜ English」双行模板的正文解析
- 备注优先取「性能/画质/帧率」，其次「补充说明」
- 支持合并 Issue 评论
"""

import json
import subprocess
import sys
import re
from datetime import datetime

REPO = "fallout1018/mac-run-apk-exe"
OUTPUT = "COMPAT.md"

# 如果你想只抓带特定 label 的 issue，改成 True；否则抓全部 open issue
FILTER_BY_LABEL = False
LABEL = "兼容"


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"⚠️  命令失败: {cmd}\n{r.stderr}", file=sys.stderr)
    return r.stdout


def fetch_issues():
    """用 gh issue list 拉取，稳定且无需拼 URL。"""
    cmd = [
        "gh", "issue", "list",
        "--repo", REPO,
        "--state", "all",
        "--limit", "500",
        "--json", "number,title,body,state,createdAt,author,labels,url,comments",
    ]
    if FILTER_BY_LABEL:
        cmd += ["--label", LABEL]
    out = run(cmd)
    if not out.strip():
        return []
    return json.loads(out)


def clean(v):
    if v is None:
        return ""
    v = str(v).strip()
    if v.lower() in ("no response", "(no response)", "_no response_", "?"):
        return ""
    return re.sub(r"[✅🔶❌]", "", v).strip()


def parse_body(body):
    """解析 GitHub 模板正文。

    支持 GitHub 渲染出的所有格式（均实测覆盖）：
      · **软件名称 ｜ App Name**          ← 加粗标题行（无值）
       红果短剧                       ← 下一行是值
      · **运行状态 ｜ Status**: ✅ 完美运行 ｜ Works perfectly   ← 标题与值同行
      · ### 字段 ｜ English\n值        ← 三级标题 + 值
      · 字段: 值  /  字段 ｜ 值         ← 纯单行

    规则：标题行 = 去掉 ** / ### 后「含 ｜（中文 ｜ English）」的行；
    值 = 标题行冒号后（同行），或下一行起直到下一个标题行。
    """
    fields = {}
    if not body:
        return fields

    def is_heading_line(stripped):
        """判断一行是否是「字段标题行」。"""
        if not stripped:
            return False
        if stripped.startswith("###"):
            return True
        # 去掉两端 **，看是否含 「中文 ｜ English」
        core = stripped.strip("*").strip()
        if "｜" in core or "|" in core:
            # ｜ 左右都有内容，且左侧以中文/字母开头
            parts = re.split(r"\s*[|｜]\s*", core, maxsplit=1)
            if len(parts) == 2 and parts[0].strip() and re.search(r"[\u4e00-\u9fa5A-Za-z]", parts[0]):
                return True
        return False

    def key_of(stripped):
        """从标题行抽出纯中文键名（去掉 **、###、英文翻译）。"""
        core = stripped.strip("*").strip()
        core = re.sub(r"^#{1,6}\s*", "", core).strip()
        core = re.split(r"\s*[|｜]\s*", core)[0].strip().strip("*").strip()
        return core

    lines = [l.strip() for l in body.splitlines()]
    cur_key = None
    buf = []

    def flush():
        nonlocal cur_key, buf
        if cur_key is None:
            return
        if cur_key not in fields and buf:
            val = " ".join(buf).strip()
            val = re.sub(r"[✅🔶❌]", "", val).strip()
            if val and val.lower() != "no response":
                fields[cur_key] = val
        cur_key, buf = None, []

    i = 0
    while i < len(lines):
        line = lines[i]
        if not line:
            i += 1
            continue
        if is_heading_line(line):
            # 保存上一字段
            flush()
            key = key_of(line)
            # 同行「键: 值」（冒号后的值，含 ｜ English 同行情况）
            core = line.strip("*").strip()
            core = re.sub(r"^#{1,6}\s*", "", core).strip()
            # 只取「第一个 ｜ 或 :」之后的内容作为同行值候选
            m = re.match(r"^.*?[|:：]\s*(.+)$", core)
            if m:
                same = m.group(1).strip().strip("*")
                # 同行值形如 「✅ 完美运行 ｜ Works perfectly」，取中文段
                v = re.split(r"\s*[|｜]\s*", same)[0].strip()
                v = re.sub(r"[✅🔶❌]", "", v).strip()
                if v and v.lower() != "no response":
                    fields[key] = v
                    cur_key, buf = None, []
                    i += 1
                    continue
            cur_key, buf = key, []
        else:
            if cur_key is not None:
                buf.append(line)
        i += 1
    flush()
    return fields


def merge_comments(issue, fields):
    """把 Issue 的评论正文也当作补充来源。"""
    for c in issue.get("comments", []):
        cf = parse_body(c.get("body", ""))
        for k, v in cf.items():
            if clean(v) and not clean(fields.get(k)):
                fields[k] = v
    return fields


def classify_status(text):
    t = clean(text).lower()
    if "瑕疵" in t or "partial" in t or "部分" in t:
        return "partial"
    if "跑不了" in t or "broken" in t or "失败" in t:
        return "broken"
    return "working"


def build_entry(issue):
    body = issue.get("body", "") or ""
    fields = parse_body(body)
    fields = merge_comments(issue, fields)

    title = issue.get("title", "") or ""

    # 兜底：从标题提取软件名
    if not clean(fields.get("软件名称")) and not clean(fields.get("App Name")):
        m = re.search(r"\[(兼容|测试)\]\s*(.+?)(?:\s*-\s*|$)", title)
        if m:
            fields["软件名称"] = m.group(2).strip()
    # 兜底：从标题推断状态
    if not clean(fields.get("运行状态")):
        fields["运行状态"] = title

    # 英文「键名翻译」占位黑名单：值若等于这些词才算占位
    _VAL_BLACKLIST = (
        "no response", "app name", "app type", "chip family", "chip variant",
        "model", "memory", "status", "performance", "notes", "type", "name",
    )

    def good(v):
        """判断一个候选值是否是真实内容（排除英文占位与空值）。"""
        vv = clean(v)
        if not vv:
            return False
        low = vv.lower()
        # 只有「值本身等于某个英文键名」（如 "Model"、"Chip Family"）才算占位
        if low in _VAL_BLACKLIST:
            return False
        return True

    def g(*keys):
        # 1) 精确匹配
        for k in keys:
            v = fields.get(k)
            if good(v):
                return clean(v)
        # 2) 包含匹配（兼容「软件名称 ｜ App Name」这类带英文后缀的键）
        for stored_key, v in fields.items():
            if not good(v):
                continue
            for k in keys:
                if k and (k in stored_key or stored_key.rstrip("s") == k):
                    return clean(v)
        return ""

    name = g("软件名称", "App Name") or "?"
    typ = g("软件类型", "App Type") or "APK/安卓应用"
    chip_family = g("芯片系列", "Chip Family")
    chip_variant = g("芯片后缀", "Chip Variant")
    chip = chip_family
    if chip and chip_variant and chip_variant != "标准版":
        chip = f"{chip} / {chip_variant}"
    chip = chip or "?"

    memory = g("内存", "Memory") or "?"
    model = g("机型", "Model") or "?"
    yyb = g("Mac 应用宝版本", "应用宝版本", "Mac Version")

    notes = g("性能 / 画质 / 帧率（可选）", "性能", "补充说明", "Notes") or "-"

    return {
        "name": name,
        "type": typ,
        "chip": chip,
        "memory": memory,
        "model": model,
        "yyb": yyb,
        "status": classify_status(g("运行状态", "Status")),
        "notes": notes.replace("\n", " ")[:80],
        "author": issue.get("author", {}).get("login", issue.get("user", {}).get("login", "?")),
    }


def md_table(rows):
    """8 列表格：软件 类型 芯片 内存 机型 应用宝版本 备注 贡献者"""
    head = (
        "| 软件 | 类型 | 芯片 | 内存 | 机型 | 应用宝版本 | 备注 | 贡献者 |\n"
        "|------|------|------|------|------|-----------|------|----------|\n"
    )
    for r in rows:
        head += (
            f"| {r['name']} | {r['type']} | {r['chip']} | {r['memory']} "
            f"| {r['model']} | {r['yyb'] or '-'} | {r['notes']} | @{r['author']} |\n"
        )
    return head


def main():
    print("📡 正在从 GitHub 拉取 Issues...")
    issues = fetch_issues()
    print(f"📋 共获取到 {len(issues)} 条 Issues")

    (working, partial, broken) = ([], [], [])
    for iss in issues:
        if iss.get("pull_request"):
            continue
        # 若没开 FILTER_BY_LABEL，则在这里按标题过滤兼容报告
        title = iss.get("title", "")
        if not FILTER_BY_LABEL and "[兼容]" not in title:
            continue
        entry = build_entry(iss)
        if entry["status"] == "partial":
            partial.append(entry)
        elif entry["status"] == "broken":
            broken.append(entry)
        else:
            working.append(entry)

    for lst in (working, partial, broken):
        lst.sort(key=lambda x: (x["model"], x["name"]))

    now = datetime.now().strftime("%Y-%m-%d")
    total = len(working) + len(partial) + len(broken)

    out = [
        "# 兼容清单 ｜ Compatibility List",
        "",
        f"> 最后更新 ｜ Last updated: {now} | 共 {total} 条记录",
        f"> 数据来源：社区 Issue 投稿 → [提交兼容报告](https://github.com/{REPO}/issues/new?template=compat-report.yml)",
        "",
        f"## ✅ 完美运行 ｜ Working ({len(working)})",
        "",
        md_table(working),
        "",
        f"## 🔶 能跑但有瑕疵 ｜ Partial ({len(partial)})",
        "",
        md_table(partial),
        "",
        f"## ❌ 跑不了 ｜ Broken ({len(broken)})",
        "",
        md_table(broken),
        "",
        "---",
        "*此文件由脚本自动生成，请勿手动编辑 ｜ Auto-generated by collect_issues.py*",
    ]

    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write("\n".join(out))

    print(f"✅ 已生成 {OUTPUT}")
    print(f"   ✅ {len(working)} ｜ 🔶 {len(partial)} ｜ ❌ {len(broken)}")


if __name__ == "__main__":
    main()
