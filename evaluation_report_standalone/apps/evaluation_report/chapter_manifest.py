"""Editable chapter list for evaluation reports (预评 / 控评)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChapterSpec:
    key: str
    title: str
    relpath: str
    group: str = "正文"  # 前置 / 正文
    kind: str = "body"  # front | body


# 预评价（YP）默认章节
EDITABLE_CHAPTERS_PRE: tuple[ChapterSpec, ...] = (
    ChapterSpec("cover", "封面", "chapters/00-cover.tex", "前置", "front"),
    ChapterSpec("qualification", "资质证书", "chapters/01-qualification.tex", "前置", "front"),
    ChapterSpec("declaration", "声明", "chapters/02-declaration.tex", "前置", "front"),
    ChapterSpec("ch1", "第1章 概述", "chapters/04-chapter1.tex", "正文", "body"),
    ChapterSpec("ch2", "第2章 工程分析", "chapters/05-chapter2.tex", "正文", "body"),
    ChapterSpec("ch3", "第3章 辐射源项", "chapters/06-chapter3.tex", "正文", "body"),
    ChapterSpec("ch4", "第4章 防护措施", "chapters/07-chapter4.tex", "正文", "body"),
    ChapterSpec("ch5", "第5章 监测计划", "chapters/08-chapter5.tex", "正文", "body"),
    ChapterSpec("ch6", "第6章 危害评价", "chapters/09-chapter6.tex", "正文", "body"),
    ChapterSpec("ch7", "第7章 应急", "chapters/10-chapter7.tex", "正文", "body"),
    ChapterSpec("ch8", "第8章 管理", "chapters/11-chapter8.tex", "正文", "body"),
    ChapterSpec("ch9", "第9章 结论与建议", "chapters/12-chapter9.tex", "正文", "body"),
)

# 控制效果评价（KP）：第5章为监测与评价；其余章名与预评骨架一致
EDITABLE_CHAPTERS_CONTROL: tuple[ChapterSpec, ...] = (
    ChapterSpec("cover", "封面", "chapters/00-cover.tex", "前置", "front"),
    ChapterSpec("qualification", "资质证书", "chapters/01-qualification.tex", "前置", "front"),
    ChapterSpec("declaration", "声明", "chapters/02-declaration.tex", "前置", "front"),
    ChapterSpec("ch1", "第1章 概述", "chapters/04-chapter1.tex", "正文", "body"),
    ChapterSpec("ch2", "第2章 分析", "chapters/05-chapter2.tex", "正文", "body"),
    ChapterSpec("ch3", "第3章 辐射源项", "chapters/06-chapter3.tex", "正文", "body"),
    ChapterSpec("ch4", "第4章 防护措施评价", "chapters/07-chapter4.tex", "正文", "body"),
    ChapterSpec("ch5", "第5章 监测与评价", "chapters/08-chapter5.tex", "正文", "body"),
    ChapterSpec("ch6", "第6章 危害评价", "chapters/09-chapter6.tex", "正文", "body"),
    ChapterSpec("ch7", "第7章 应急", "chapters/10-chapter7.tex", "正文", "body"),
    ChapterSpec("ch8", "第8章 管理", "chapters/11-chapter8.tex", "正文", "body"),
    ChapterSpec("ch9", "第9章 结论与建议", "chapters/12-chapter9.tex", "正文", "body"),
)

# 兼容旧引用
EDITABLE_CHAPTERS: tuple[ChapterSpec, ...] = EDITABLE_CHAPTERS_PRE


def editable_chapters_for(template_key: str | None = None) -> tuple[ChapterSpec, ...]:
    key = (template_key or "").strip().lower()
    if key.startswith("kp") or "control" in key or "kongping" in key:
        return EDITABLE_CHAPTERS_CONTROL
    return EDITABLE_CHAPTERS_PRE


def chapter_by_key(key: str, template_key: str | None = None) -> ChapterSpec | None:
    for ch in editable_chapters_for(template_key):
        if ch.key == key:
            return ch
    return None
