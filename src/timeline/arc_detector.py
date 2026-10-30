from typing import List, Tuple
from src.config import yaml_config
from src.schemas.extraction import ChapterExtraction

class ArcDetector:
    """Thuật toán tính điểm ranh giới Arc (Changepoint Scoring)"""
    def __init__(self):
        cfg = yaml_config.get("arc_detection", {})
        self.weights = cfg.get("weights", {
            "map_change": 0.40,
            "realm_breakthrough": 0.30,
            "major_conflict": 0.20,
            "time_skip": 0.10
        })
        self.score_threshold = cfg.get("score_threshold", 0.60)
        self.min_arc_length = cfg.get("min_arc_length", 30)

    def calculate_score(self, chapter: ChapterExtraction) -> float:
        score = 0.0
        if chapter.is_map_change:
            score += self.weights.get("map_change", 0.40)
        if chapter.is_realm_breakthrough:
            score += self.weights.get("realm_breakthrough", 0.30)
        if chapter.is_major_conflict:
            score += self.weights.get("major_conflict", 0.20)
        if chapter.time_skip_years >= 5.0:
            score += self.weights.get("time_skip", 0.10)
        return score

    def segment_arcs(self, chapters: List[ChapterExtraction]) -> List[Tuple[int, int]]:
        ranges = []
        start_idx = 0
        n = len(chapters)

        for i in range(1, n):
            current_len = i - start_idx
            score = self.calculate_score(chapters[i])
            if score >= self.score_threshold and current_len >= self.min_arc_length:
                ranges.append((chapters[start_idx].chapter_id, chapters[i - 1].chapter_id))
                start_idx = i

        if start_idx < n:
            ranges.append((chapters[start_idx].chapter_id, chapters[-1].chapter_id))
        return ranges
