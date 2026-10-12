#!/usr/bin/env python3
"""Generate demo stage-2 decision files from the current collection.

These are hand-written, not model output. They exist so the repository ships a
working example of both decision formats, and so `site/digest.html` shows what
stage 2 produces without anyone needing to call a model first.

Real usage replaces these with actual model output.

    python tools/demo_decisions.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ai_stage  # noqa: E402

COLLECTED = Path("data/collected")
AIDIR = Path("data/ai")


def find(items: dict[str, dict], needle: str, channel: str | None = None) -> str | None:
    for key, entry in items.items():
        if needle in entry["title"] and (channel is None or (entry.get("extra") or {}).get("channel") == channel):
            return key
    return None


def main() -> int:
    path = COLLECTED / "telegram.json"
    if not path.exists():
        print(f"{path} not found; run tools/collect.py first", file=sys.stderr)
        return 1

    items = {ai_stage.item_id(e): e for e in json.loads(path.read_text(encoding="utf-8"))["items"]}
    day = json.loads((COLLECTED / "index.json").read_text(encoding="utf-8")).get("day", "")

    oneplus_m = find(items, "一加 16", "MaiTungNews")
    oneplus_g = find(items, "一加 16", "GodlyNews1")
    kuke_g = find(items, "库克", "GodlyNews1")
    kuke_x = find(items, "库克", "xhqcankao")
    trump_t = find(items, "柴油", "tnews365")
    trump_d = find(items, "柴油", "DNSPODT")

    actions = []
    if oneplus_m and oneplus_g:
        actions.append({
            "op": "merge", "primary_id": oneplus_m, "merge_ids": [oneplus_g],
            "title": "一加 16 正式发布：第六代骁龙 8 超级至尊版，185Hz 高刷，5999 元起",
            "summary": "一加 16 于 10 月 13 日 10:00 正式开售，首销期至 11 月 13 日。搭载第六代骁龙 8 超级至尊版芯片，"
                       "全球首发 185Hz 极速高刷，并配备全自研「电竞三芯」。定价 5999 元起，国补后 5199 元起；"
                       "四个版本分别为 12+256GB 5999 元、12+512GB 6799 元、16+512GB 7499 元、16+1TB 8499 元，"
                       "首销价与区域补贴价另有下调。",
            "topics": ["technology"], "confidence": "high",
        })
    if kuke_g and kuke_x:
        actions.append({
            "op": "drop", "id": kuke_x, "canonical_id": kuke_g,
            "reason": "同一新华社通稿，措辞与段落完全一致，无独有信息",
        })
    if trump_t and trump_d:
        actions.append({
            "op": "merge", "primary_id": trump_t, "merge_ids": [trump_d],
            "title": "特朗普宣称俄乌达成「能源停火」，乌克兰与欧洲盟友均未确认",
            "summary": "特朗普表示俄乌已同意立即实施能源停火，并称全球柴油价格上涨主要由俄乌战争引起、而非伊朗；"
                       "他还建议乌克兰更换领导人。乌克兰方面称尚未收到停火确认，强调任何降级须双向对等，"
                       "泽连斯基提出若俄方实现 60 天全面停火则愿组织选举。英德法等欧洲盟友重申支持乌克兰并将继续加大施压。"
                       "停火说法公布后，哈尔科夫一座供暖和发电厂仍遭俄军袭击。",
            "topics": ["policy", "society"], "confidence": "medium",
        })

    AIDIR.mkdir(parents=True, exist_ok=True)
    (AIDIR / "telegram_decisions.json").write_text(json.dumps({
        "schema": ai_stage.TELEGRAM_SCHEMA,
        "day": day,
        "model": "demo-decisions (hand-written example, not model output)",
        "actions": actions,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def ids(*keys):
        return [k for k in keys if k]

    highlights = [
        {"item_ids": ids(find(items, "诺贝尔经济学奖"), find(items, "诺贝尔和平奖")),
         "rank": 1, "title": "2026 年诺贝尔经济学奖与和平奖揭晓",
         "summary": "瑞典皇家科学院将经济学奖授予罗伯特·霍尔与迈克尔·伍德福德，表彰其奠定宏观经济稳定政策的微观经济学基础；"
                    "和平奖授予纳瓦内特姆·皮莱，表彰其为促进和平与国际法所作的努力。",
         "topics": ["society", "policy"], "importance": 90,
         "why": "两家独立频道分别报道，同日两大奖项"},
        {"item_ids": ids(find(items, "中欧")), "rank": 2,
         "title": "中欧就混动与电动汽车贸易达成谅解",
         "summary": "双方就混动汽车与电动汽车贸易形成多项共识，涉及配额安排与市场准入，是中欧经贸关系的重要节点。",
         "topics": ["business", "policy"], "importance": 82, "why": "政策级事件，涉及产业与贸易"},
        {"item_ids": ids(find(items, "Maia 300")), "rank": 3,
         "title": "微软发布第三代自研 AI 芯片 Maia 300",
         "summary": "微软推出第三代自研 AI 芯片 Maia 300，继续推进自研算力路线。",
         "topics": ["ai", "technology"], "importance": 78, "why": "单源但行业影响重大"},
        {"item_ids": ids(find(items, "一加 16", "MaiTungNews"), find(items, "一加 16", "GodlyNews1")),
         "rank": 4, "title": "一加 16 正式发布，5999 元起",
         "summary": "搭载第六代骁龙 8 超级至尊版芯片，全球首发 185Hz 极速高刷，配备全自研「电竞三芯」，"
                    "10 月 13 日开售，国补后 5199 元起。",
         "topics": ["technology"], "importance": 74, "why": "多源报道，定价与开售时间明确"},
        {"item_ids": ids(find(items, "库克", "GodlyNews1")), "rank": 5,
         "title": "何立峰会见苹果执行董事长蒂姆·库克",
         "summary": "何立峰在人民大会堂会见库克，表示欢迎包括苹果在内的各国科技企业深化对华合作；"
                    "库克称对在华长期发展充满信心。",
         "topics": ["business", "policy"], "importance": 70, "why": "多源报道，中美经贸信号"},
        {"item_ids": ids(find(items, "Linux内核")), "rank": 6,
         "title": "Linux 创始人用 AI 完成重大内核改进",
         "summary": "Linux 创始人借助 AI 工具完成了一项重大的 Linux 内核改进。",
         "topics": ["technology", "ai"], "importance": 62, "why": "单源，但具标志性意义"},
        {"item_ids": ids(find(items, "Arena发布")), "rank": 7,
         "title": "Arena 第 41 周大模型榜单：the model 5.5 Max 登顶智能体榜",
         "summary": "Arena 发布第 41 周大模型榜单，the model 5.5 Max 首次进入智能体榜并登顶。",
         "topics": ["ai"], "importance": 58, "why": "单源，行业基准参考"},
    ]
    highlights = [h for h in highlights if h["item_ids"]]

    themes = [
        {"name": "AI 算力与模型竞赛",
         "summary": "芯片、榜单、模型能力三条线同日推进，厂商竞争集中在算力与智能体能力上。",
         "item_ids": ids(find(items, "Maia 300"), find(items, "Arena发布"))},
        {"name": "中欧经贸与跨国企业",
         "summary": "电动汽车贸易谅解与苹果高管访华同日出现，指向经贸关系的同步调整。",
         "item_ids": ids(find(items, "中欧"), find(items, "库克", "GodlyNews1"))},
    ]
    themes = [t for t in themes if t["item_ids"]]

    (AIDIR / "digest_decisions.json").write_text(json.dumps({
        "schema": ai_stage.DIGEST_SCHEMA,
        "day": day,
        "model": "demo-decisions (hand-written example, not model output)",
        "headline": "诺贝尔奖集中揭晓，一加 16 与微软 Maia 300 同日登场，中欧就电动汽车贸易达成谅解。",
        "highlights": highlights,
        "themes": themes,
        "stats_note": f"当日 7 个频道共 {len(items)} 条消息，其中 3 组为跨频道重复，已合并；科技与政策类占多数。",
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"wrote {AIDIR}/telegram_decisions.json ({len(actions)} actions)")
    print(f"wrote {AIDIR}/digest_decisions.json ({len(highlights)} highlights, {len(themes)} themes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
