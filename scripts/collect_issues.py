#!/usr/bin/env python3
"""
collect_issues.py - 从 GitHub Issues 汇总兼容报告到 COMPAT.md
用法：
  python3 scripts/collect_issues.py              # 自动通过 gh CLI 拉取
  python3 scripts/collect_issues.py --input issues.json  # 从本地 JSON 读取
"""

import json
import subprocess
import sys
import re
from datetime import datetime

# ========= 配置 =========
REPO = "fallout1018/mac-run-apk-exe"
OUTPUT = "COMPAT.md"


def fetch_issues():
    """通过 gh CLI 拉取 issues（不过滤 label，由标题过滤）"""
    cmd = [
        "gh", "issue", "list",
        "--repo", REPO,
        "--state", "all",
        "--limit", "500",
        "--json", "number,title,body,state,createdAt,author,labels,url"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"❌ gh CLI 执行失败：{result.stderr}")
        sys.exit(1)
    return json.loads(result.stdout)


def parse_body(body):
    """
    解析 Issue 正文。
    兼容 GitHub 表单的两种格式：
      1) ### 字段名\n值
      2) 字段名 | Field Name\n值   （双行）
    值里的 emoji(✅🔶❌) 与 "No response" 会被清理。
    """
    fields = {}
    if not body:
        return fields

    # 先按 "### 字段名" 分割（GitHub YAML 模板常用格式）
    sections = re.split(r"^### ", body, flags=re.MULTILINE)
    for sec in sections:
        if not sec.strip():
            continue
        lines = sec.split("\n")
        key = lines[0].strip()
        # 去掉 " | English" 后缀，只保留中文键名
        key = key.split("|")[0].strip()
        value_lines = [l.strip() for l in lines[1:] if l.strip() and not l.strip().startswith("<!--")]
        value = " ".join(value_lines)
        # 清理 emoji 与 "No response"
        value = re.sub(r"[✅🔶❌]", "", value).strip()
        if value.lower() == "no response":
            value = ""
        if key:
            fields[key] = value
    return fields


def classify_status(status):
    """兼容中英文状态描述"""
    s = str(status).lower()
    if "完美" in s or "works perfectly" in s or "working" in s:
        return "working"
    if "瑕疵" in s or "partial" in s:
        return "partial"
    if "跑不了" in s or "broken" in s:
        return "broken"
    return "unknown"


def build_row(entry):
    return (
        f"| {entry.get('name', '?')} "
        f"| {entry.get('type', '?')} "
        f"| {entry.get('chip', '?')} "
        f"| {entry.get('memory', '?')} "
        f"| {entry.get('model', '?')} "
        f"| {entry.get('yyb_version', '?')} "
        f"| {entry.get('notes', '-')} "
        f"| @{entry.get('author', '?')} |"
    )


def main():
    # 读取数据
    if "--input" in sys.argv:
        idx = sys.argv.index("--input")
        with open(sys.argv[idx + 1], "r", encoding="utf-8") as f:
            issues = json.load(f)
    else:
        print("📡 正在从 GitHub 拉取 Issues...")
        issues = fetch_issues()

    print(f"📋 共获取到 {len(issues)} 条 Issues")

    working, partial, broken = [], [], []

    for issue in issues:
        if not issue.get("body"):
            continue

        # 只处理标题含 [兼容] 的 Issue（兼容报告）
        if "[兼容]" not in issue.get("title", ""):
            continue

        fields = parse_body(issue["body"])

        # 兜底：从标题提取软件名
        name = fields.get("软件名称", "").strip()
        if not name:
            name = issue["title"].split("-")[0].replace("[兼容]", "").strip()
        if not name:
            continue

        # 兜底：从标题/正文推断运行状态
        status_val = fields.get("运行状态", "")
        if not status_val:
            if "完美运行" in issue["title"]:
                status_val = "完美运行"
            elif "有瑕疵" in issue["title"]:
                status_val = "有瑕疵"
            elif "跑不了" in issue["title"]:
                status_val = "跑不了"
        # emoji 已被 parse_body 清掉，这里再保险一次
        status_val = re.sub(r"[✅🔶❌]", "", status_val).strip()

        chip_family = fields.get("芯片系列", "")
        chip_variant = fields.get("芯片后缀", "")
        chip = chip_family
        if chip_variant and chip_variant != "标准版":
            chip = f"{chip_family} / {chip_variant}"

        entry = {
            "name": name,
            "type": fields.get("软件类型", ""),
            "chip": chip,
            "model": fields.get("机型", ""),
            "memory": fields.get("内存", ""),
            "yyb_version": fields.get("Mac 应用宝版本", ""),
            "status": classify_status(status_val),
            "notes": fields.get("补充说明", "").replace("\n", " ")[:80],
            "author": issue["author"]["login"],
            "url": issue["url"],
            "date": issue["createdAt"][:10],
        }

        if entry["status"] == "working":
            working.append(entry)
        elif entry["status"] == "partial":
            partial.append(entry)
        elif entry["status"] == "broken":
            broken.append(entry)

    # 生成 COMPAT.md
    now = datetime.now().strftime("%Y-%m-%d")
    total = len(working) + len(partial) + len(broken)

    lines = [
        "# 兼容清单 ｜ Compatibility List",
        "",
        f"> 最后更新 ｜ Last updated: {now} | 共 {total} 条记录 ｜ {total} entries",
        f"> 数据来源：社区 Issue 投稿，欢迎提交 → [New Issue](https://github.com/{REPO}/issues/new?template=compat-report.yml)",
        "",
        f"## ✅ 完美运行 ｜ Working ({len(working)})",
        "",
        "| 软件 ｜ App | 类型 ｜ Type | 芯片 ｜ Chip | 内存 ｜ Memory | 机型 ｜ Model | 应用宝版本 ｜ YYB | 备注 ｜ Notes | 贡献者 ｜ Contributor |",
        "|------|------|------|------|------|------|------|--------|",
    ]
    for e in working:
        lines.append(build_row(e))

    lines += [
        "",
        f"## 🔶 能跑但有瑕疵 ｜ Partial ({len(partial)})",
        "",
        "| 软件 ｜ App | 类型 ｜ Type | 芯片 ｜ Chip | 内存 ｜ Memory | 机型 ｜ Model | 应用宝版本 ｜ YYB | 备注 ｜ Notes | 贡献者 ｜ Contributor |",
        "|------|------|------|------|------|------|------|--------|",
    ]
    for e in partial:
        lines.append(build_row(e))

    lines += [
        "",
        f"## ❌ 跑不了 ｜ Broken ({len(broken)})",
        "",
        "| 软件 ｜ App | 类型 ｜ Type | 芯片 ｜ Chip | 内存 ｜ Memory | 机型 ｜ Model | 应用宝版本 ｜ YYB | 备注 ｜ Notes | 贡献者 ｜ Contributor |",
        "|------|------|------|------|------|------|------|--------|",
    ]
    for e in broken:
        lines.append(build_row(e))

    lines += [
        "",
        "---",
        "*此文件由脚本自动生成，请勿手动编辑 ｜ Auto-generated by collect_issues.py, do not edit manually.*",
    ]

    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"✅ 已生成 {OUTPUT}")
    print(f"   ✅ {len(working)} 条 ｜ 🔶 {len(partial)} 条 ｜ ❌ {len(broken)} 条")


if __name__ == "__main__":
    main()
