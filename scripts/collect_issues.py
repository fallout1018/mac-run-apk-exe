#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
collect_issues.py - 从 GitHub Issues 汇总兼容报告，生成 COMPAT.md 并同步 README 预览
用法：python3 scripts/collect_issues.py

依赖：gh CLI（已登录：gh auth login）、python3
"""
import os
import re
import sys
import json
import subprocess
from datetime import datetime

# ==================== 配置 ====================
REPO = "fallout1018/mac-run-apk-exe"
OUT_FILE = "COMPAT.md"

# 开关：
#   FILTER_BY_LABEL = True  时，只抓带 LABEL 标签的 Issue
#   FILTER_BY_LABEL = False 时，抓所有 open Issue，靠标题里的 [兼容] 过滤
FILTER_BY_LABEL = False
LABEL = "兼容"

# README 首页「完美运行」预览条数；设为 0 = 只放跳转链接不放表
PREVIEW_LIMIT = 5

# gh issue list --json 请求的字段（必须全部在可用字段列表内：author/body/createdAt/labels/number/state/title/url ...）
# 注意：是 "url"，不是 "html_url"
JSON_FIELDS = "number,title,body,state,createdAt,author,labels,url"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)


# ==================== 工具 ====================
def run_cmd(cmd):
    """执行命令并返回 stdout；失败时打印 stderr 并返回空字符串"""
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"⚠️ 命令失败: {cmd}\n{r.stderr.strip()}")
            return ""
        return r.stdout
    except Exception as e:
        print(f"⚠️ 执行异常: {e}")
        return ""


def fetch_issues():
    """拉取 Issues 列表"""
    if FILTER_BY_LABEL:
        q = f'gh issue list --repo "{REPO}" --label "{LABEL}" --state all --limit 500 --json {JSON_FIELDS}'
    else:
        q = f'gh issue list --repo "{REPO}" --state all --limit 500 --json {JSON_FIELDS}'
    out = run_cmd(q)
    if not out:
        return []
    try:
        return json.loads(out)
    except json.JSONDecodeError as e:
        print(f"⚠️ JSON 解析失败: {e}\n原始输出: {out[:300]}")
        return []


def fetch_comments(issue_number):
    """拉取某 Issue 的评论（用于补充字段）"""
    out = run_cmd(f'gh api repos/{REPO}/issues/{issue_number}/comments')
    if not out:
        return []
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return []


def clean(v):
    """清洗字段值：去 markdown 加粗、去首尾空白、把 No response 当空"""
    if v is None:
        return ""
    s = str(v).strip()
    s = s.replace("**", "").replace("*", "")
    if s.lower() in ("no response", "(no response)", "_no response_", "?", "none"):
        return ""
    return s


def is_heading(text):
    """判断一行是否是「字段标题行」。

    GitHub 表单的标题行一定是「**加粗 ｜ 英文**」格式，所以判定规则：
      1. 整行被 **...** 包裹（去掉首尾 ** 后仍有内容）
      2. 去掉 ** 后含至少一个中文字符
    这样可避免把纯值行（如「红果短剧」「完美运行，摸鱼刷起来」）误判成标题。
    """
    t = text.strip()
    if not t.startswith("**") or not t.endswith("**"):
        return False
    inner = t[2:-2].strip()
    if not inner:
        return False
    return bool(re.search(r"[\u4e00-\u9fff]", inner))


def parse_body(body):
    """解析 Issue 正文（GitHub 加粗双行格式），返回字段字典"""
    fields = {}
    if not body:
        return fields

    # —— 策略 1：按行解析（兼容双行 + 同行值）——
    lines = body.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if is_heading(line):
            # 值可能在同行（末尾 : 值）或下一行
            val = ""
            # 同行值：**运行状态 ｜ Status**: ✅ 完美运行
            m = re.match(r"^.*?[:：]\s*(.+)$", line)
            if m and not line.endswith((":", "：")):
                val = m.group(1).strip()
            # 下一行值
            if not val and i + 1 < len(lines):
                nxt = lines[i + 1].strip()
                if nxt and not is_heading(nxt) and not nxt.startswith("###"):
                    val = nxt
                    i += 1
            key = line.replace("**", "").strip()
            fields[key] = val
        i += 1

    # —— 策略 2：精确键名映射（脚本内部用的统一键）——
    def g(*candidates, default=""):
        """按候选标题子串查找值，返回清洗后的结果；找不到返回 default"""
        for cand in candidates:
            for k, v in fields.items():
                # 精确匹配标题，或标题包含候选词
                if k == cand or cand.lower() in k.lower():
                    cv = clean(v)
                    if cv:
                        return cv
        return default

    return {
        "name": g("软件名称", "App Name"),
        "type": g("软件类型", "App Type", default="APK/安卓应用"),
        "chip": g("芯片系列", "Chip Family"),
        "chip_variant": g("芯片后缀", "Chip Variant"),
        "memory": g("内存", "Memory"),
        "model": g("机型", "Model"),
        "yyb": g("Mac 应用宝版本", "Mac 应用宝 Version", "应用宝版本", "Version"),
        "status_raw": g("运行状态", "Status"),
        "perf": g("性能", "帧率", "Performance"),
        "notes": g("补充说明", "Notes"),
    }


def merge_comments(issue, fields):
    """用评论内容补充正文缺失的字段"""
    try:
        comments = fetch_comments(issue.get("number"))
        for c in comments:
            cf = parse_body(c.get("body", ""))
            for k, v in cf.items():
                if v and not fields.get(k):
                    fields[k] = v
    except Exception:
        pass
    return fields


def classify_status(text):
    """根据正文/标题判断运行状态"""
    t = (text or "").lower()
    if "🔶" in text or "瑕疵" in text or "partial" in t or "部分" in t:
        return "partial"
    if "❌" in text or "跑不了" in text or "broken" in t or "失败" in t:
        return "broken"
    return "working"  # 默认完美运行


def build_entry(issue):
    """把一个 Issue 转成表格行字典"""
    fields = parse_body(issue.get("body", ""))
    fields = merge_comments(issue, fields)

    title = issue.get("title", "") or ""

    # 软件名兜底：从标题 [兼容] 红果短剧 - 完美运行 提取
    name = fields.get("name") or ""
    if not name:
        m = re.search(r"\[\s*兼容\s*\]\s*(.+?)(?:\s*-\s*|\s*$)", title)
        if m:
            name = m.group(1).strip()
    if not name:
        name = title.split("-")[0].strip().strip("[]")
    name = clean(name) or "?"

    # 运行状态兜底
    status_raw = fields.get("status_raw") or ""
    if not status_raw:
        status_raw = title
    status = classify_status(status_raw)

    # 芯片：系列 + 后缀（如 M2 + Pro -> M2 / Pro）
    chip = clean(fields.get("chip")) or "?"
    chip_variant = clean(fields.get("chip_variant"))
    if chip_variant and chip_variant not in ("标准版", "Standard", "?"):
        chip = f"{chip} / {chip_variant}"

    # 备注：优先「性能/画质/帧率」，其次「补充说明」
    notes = clean(fields.get("perf")) or clean(fields.get("notes")) or "-"
    notes = notes.replace("\n", " ")[:80]

    # 贡献者：author（gh issue list 字段是 author，不是 user）
    author = issue.get("author") or issue.get("user") or {}
    if isinstance(author, dict):
        login = author.get("login", "?")
    else:
        login = str(author)
    contributor = "@" + login

    return {
        "name": name,
        "type": clean(fields.get("type")) or "APK/安卓应用",
        "chip": chip,
        "memory": clean(fields.get("memory")) or "?",
        "model": clean(fields.get("model")) or "?",
        "yyb": clean(fields.get("yyb")) or "",
        "status": status,
        "notes": notes,
        "contributor": contributor,
    }


# ==================== 渲染 ====================
def md_table(rows):
    """渲染 8 列表格（表头 + 数据行对齐）"""
    head = (
        "| 软件 | 类型 | 芯片 | 内存 | 机型 | 应用宝版本 | 备注 | 贡献者 |\n"
        "|------|------|------|------|------|-----------|------|--------|\n"
    )
    body = ""
    for r in rows:
        yyb = r["yyb"] if r["yyb"] else "-"
        body += (
            f"| {r['name']} | {r['type']} | {r['chip']} | {r['memory']} "
            f"| {r['model']} | {yyb} | {r['notes']} | {r['contributor']} |\n"
        )
    return head + body


def build_compat_md(working, partial, broken):
    """生成完整 COMPAT.md 内容"""
    now = datetime.now().strftime("%Y-%m-%d")
    total = len(working) + len(partial) + len(broken)
    out = []
    out.append("# 兼容清单 ｜ Compatibility List")
    out.append("")
    out.append(f"> 最后更新 ｜ Last updated: {now} · 共 {total} 条记录")
    out.append(f"> 数据来源：社区 Issue 投稿 → [提交兼容报告](https://github.com/{REPO}/issues/new?template=compat-report.yml)")
    out.append("")
    out.append(f"## ✅ 完美运行 ｜ Working ({len(working)})")
    out.append("")
    out.append(md_table(working))
    out.append("")
    out.append(f"## 🔶 能跑但有瑕疵 ｜ Partial ({len(partial)})")
    out.append("")
    out.append(md_table(partial))
    out.append("")
    out.append(f"## ❌ 跑不了 ｜ Broken ({len(broken)})")
    out.append("")
    out.append(md_table(broken))
    out.append("")
    out.append("---")
    out.append("*此文件由脚本自动生成，请勿手动编辑 ｜ Auto-generated by collect_issues.py*")
    return "\n".join(out)


# ==================== README 预览注入 ====================
def update_readme_preview(compat_md, readme_path, limit=PREVIEW_LIMIT):
    """把「完美运行」前 limit 条注入 README 的 COMPAT_PREVIEW 标记区间"""
    if not os.path.exists(readme_path):
        print(f"⚠️ 未找到 README: {readme_path}（跳过预览更新）")
        return

    # 从 compat_md 里抽取 working 表格行（跳过表头前两行）
    lines = compat_md.splitlines()
    table_rows = []
    in_working = False
    for ln in lines:
        if ln.startswith("## ✅"):
            in_working = True
            continue
        if in_working:
            if ln.startswith("## ") or ln.startswith("---"):
                break
            if ln.startswith("|") and "软件" not in ln and "------" not in ln:
                table_rows.append(ln)

    preview = table_rows[:limit] if limit > 0 else []
    if limit > 0 and not preview:
        preview = table_rows  # 没有足够条数就用全部

    if limit == 0 or not preview:
        block = (
            "> 📊 完整兼容清单见 **[COMPAT.md](./COMPAT.md)** —— "
            "哪些 APK / EXE 能跑、跑得怎么样，**大家共同维护**。\n"
        )
    else:
        head = "| 软件 | 类型 | 芯片 | 内存 | 机型 | 备注 | 贡献者 |\n|------|------|------|------|------|------|--------|\n"
        body = "\n".join(preview)
        block = (
            f"> 📊 完整清单见 **[COMPAT.md](./COMPAT.md)**（共 ✅{len(table_rows)} 条完美运行）\n\n"
            + head + body + "\n\n"
            + "> 想加入？[提交一条兼容报告](https://github.com/{REPO}/issues/new?template=compat-report.yml) 即可。\n".format(REPO=REPO)
        )

    start_marker = "<!-- COMPAT_PREVIEW:start -->"
    end_marker = "<!-- COMPAT_PREVIEW:end -->"
    with open(readme_path, "r", encoding="utf-8") as f:
        content = f.read()

    if start_marker not in content or end_marker not in content:
        print(f"⚠️ README 未找到 {start_marker} / {end_marker} 标记，跳过注入")
        return

    pre = content.split(start_marker)[0]
    post = content.split(end_marker)[-1]
    new_content = pre + start_marker + "\n\n" + block + "\n" + end_marker + post

    with open(readme_path, "w", encoding="utf-8") as f:
        f.write(new_content)
    print(f"✅ 已更新 README.md 兼容预览（{len(preview)} 条）")


# ==================== 主流程 ====================
def main():
    print("📦 正在从 GitHub 拉取 Issues...")

    raw_issues = fetch_issues()
    # 兼容两种数据结构：list，或 {"items": [...]}
    if isinstance(raw_issues, dict):
        raw_issues = raw_issues.get("items", [])

    # 过滤：标题含 [兼容]（FILTER_BY_LABEL=False 时）；排除 PR
    issues = []
    for iss in raw_issues:
        if iss.get("pull_request"):
            continue
        title = iss.get("title", "") or ""
        if not FILTER_BY_LABEL and "[兼容]" not in title:
            continue
        issues.append(iss)

    print(f"📋 共获取到 {len(issues)} 条 Issues")

    working, partial, broken = [], [], []
    for iss in issues:
        try:
            entry = build_entry(iss)
        except Exception as e:
            print(f"⚠️ 解析 Issue #{iss.get('number')} 失败: {e}")
            continue
        if entry["status"] == "partial":
            partial.append(entry)
        elif entry["status"] == "broken":
            broken.append(entry)
        else:
            working.append(entry)

    for lst in (working, partial, broken):
        lst.sort(key=lambda x: (x["model"], x["name"]))

    compat_md = build_compat_md(working, partial, broken)

    # 写 COMPAT.md
    compat_path = os.path.join(ROOT_DIR, OUT_FILE)
    with open(compat_path, "w", encoding="utf-8") as f:
        f.write(compat_md)
    print(f"✅ 已生成 {OUT_FILE}")
    print(f"   ✅ {len(working)} ｜ 🔶 {len(partial)} ｜ ❌ {len(broken)}")

    # 同步 README 预览
    readme_path = os.path.join(ROOT_DIR, "README.md")
    update_readme_preview(compat_md, readme_path, limit=PREVIEW_LIMIT)


if __name__ == "__main__":
    main()
