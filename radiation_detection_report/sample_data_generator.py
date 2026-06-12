"""
随机生成检测点数据 JSON（复杂点位按子行计数编号）。
"""

from __future__ import annotations

import argparse
import json
import os
import random
from typing import Any, Dict, List, Optional

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SAMPLE_OUT = os.path.join(PACKAGE_DIR, "examples", "sample_data.json")

SUB_LABELS = ["中部", "上端", "下端", "左侧", "右侧", "表面"]
COMPLEX_NAMES = [
    "铅玻璃观察窗",
    "操作屏观察窗",
    "控制室防护门",
    "电缆穿墙孔",
    "通风管道穿越处",
    "患者等候椅旁",
]
COMPLEX_SUFFIXES = ["C外表面", "M1机位处", "门外侧", "内侧", "操作位旁"]
COMPLEX_DISTANCES = ["30cm", "50cm", "100cm"]


def format_complex_location(name: str, distance: str) -> str:
    """复杂点位主格：点位名称+距离，距离接在末尾（不用括号、不换行）。"""
    name = name.strip()
    distance = distance.strip()
    if not distance:
        return name
    return f"{name}{distance}"
SIMPLE_PREFIX = [
    "工作人员操作位",
    "观察室门外侧",
    "机房门外侧",
    "走廊东侧",
    "走廊西侧",
    "配电间门口",
    "废物暂存间门口",
    "楼梯口附近",
    "候诊区座椅旁",
    "设备机房南侧",
]


def _rand_result() -> str:
    return f"{random.uniform(0.14, 0.28):.2f}"


def generate_points(total: int = 100, seed: Optional[int] = None) -> List[Dict[str, Any]]:
    if seed is not None:
        random.seed(seed)
    points: List[Dict[str, Any]] = []
    next_id = 1
    while next_id <= total:
        remaining = total - next_id + 1
        use_complex = remaining >= 2 and random.random() < 0.38
        if use_complex:
            max_subs = min(5, remaining)
            n_subs = random.randint(2, max_subs)
            subs = []
            for _ in range(n_subs):
                subs.append(
                    {
                        "id": str(next_id),
                        "location_sub": random.choice(SUB_LABELS),
                        "result": _rand_result(),
                        "standard": "≤2.5",
                        "evaluation": "合格",
                    }
                )
                next_id += 1
            base = random.choice(COMPLEX_NAMES)
            suffix = random.choice(COMPLEX_SUFFIXES)
            distance = random.choice(COMPLEX_DISTANCES)
            location = format_complex_location(f"{base}{suffix}", distance)
            points.append({"type": "complex", "location": location, "sub_rows": subs})
        else:
            loc = random.choice(SIMPLE_PREFIX)
            suffix = random.choice(["", "A区", "B区", "北侧", "南侧", "一层", "二层"])
            points.append(
                {
                    "type": "simple",
                    "id": str(next_id),
                    "location": f"{loc}{suffix}",
                    "result": _rand_result(),
                    "standard": "≤2.5",
                    "evaluation": "合格",
                }
            )
            next_id += 1
    return points


def build_report(total: int = 100, seed: Optional[int] = 42) -> Dict[str, Any]:
    points = generate_points(total=total, seed=seed)
    used_ids = []
    for p in points:
        if p["type"] == "simple":
            used_ids.append(int(p["id"]))
        else:
            used_ids.extend(int(s["id"]) for s in p["sub_rows"])
    assert len(used_ids) == total, f"expected {total} ids, got {len(used_ids)}"
    assert sorted(used_ids) == list(range(1, total + 1)), "ids must be 1..100 sequential"

    return {
        "condition": "检测条件：CT 螺旋扫描，120kV，200mA，9.7s；散射模体：CT 体部剂量模体",
        "points": points,
        "background": {"label": "本底值（μSv/h）", "value": "0.14～0.15"},
        "notes": [
            "注：1.上表中检测结果未扣除本底值；检测时间按仪器响应时间，并按约定值折算时间对应剂量率。",
            "2.检测点下方无建筑物，人员无法到达处不参与评价。",
        ],
        "meta": {
            "total_point_ids": total,
            "simple_groups": sum(1 for p in points if p["type"] == "simple"),
            "complex_groups": sum(1 for p in points if p["type"] == "complex"),
            "seed": seed,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate random 100-point radiation table JSON.")
    parser.add_argument("--output", default=DEFAULT_SAMPLE_OUT)
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    data = build_report(total=args.count, seed=args.seed)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"JSON written: {args.output}")
    print(
        f"  simple groups: {data['meta']['simple_groups']}, "
        f"complex groups: {data['meta']['complex_groups']}, "
        f"total ids: {data['meta']['total_point_ids']}"
    )


if __name__ == "__main__":
    main()
