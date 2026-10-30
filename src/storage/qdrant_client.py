from qdrant_client import QdrantClient
from qdrant_client.http import models
from src.config import settings, yaml_config
from typing import List, Dict, Any

class QdrantManager:
    """Quản trị các vector collections trên Qdrant"""
    def __init__(self):
        self.client = QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)
        self.collections = yaml_config.get("qdrant", {}).get("collections", {
            "chapters": "pntt_chapters",
            "arcs": "pntt_arcs",
            "entities": "pntt_entities"
        })
        self.init_collections()

    def init_collections(self):
        vector_size = yaml_config.get("qdrant", {}).get("vector_size", 1024)
        for _, coll_name in self.collections.items():
            if not self.client.collection_exists(collection_name=coll_name):
                self.client.create_collection(
                    collection_name=coll_name,
                    vectors_config=models.VectorParams(size=vector_size, distance=models.Distance.COSINE)
                )

    def insert_arc(self, arc_id: int, vector: List[float], payload: Dict[str, Any]):
        self.client.upsert(
            collection_name=self.collections["arcs"],
            points=[models.PointStruct(id=arc_id, vector=vector, payload=payload)]
        )
