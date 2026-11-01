"""Query graph: route, bound, resolve_entities, retrieve, gate, synthesize, verify."""

from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Any, Optional

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from xn.graph.deps import GraphDeps
from xn.graph.nodes.bound import bound
from xn.graph.nodes.gate import gate
from xn.graph.nodes.resolve import resolve_entities
from xn.graph.nodes.retrieve import retrieve
from xn.graph.nodes.route import route
from xn.graph.nodes.synthesize import synthesize
from xn.graph.nodes.verify import verify
from xn.schemas.state import QueryState


def _continue_unless_refused(next_node: str) -> Any:
    """Build an edge condition that ends the graph once a node has refused."""

    def choose(state: QueryState) -> str:
        return END if state.get("refusal") else next_node

    return choose


def open_checkpointer(path: Path) -> SqliteSaver:
    """Open the SqliteSaver at path, creating its directory and tables."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    saver = SqliteSaver(sqlite3.connect(str(path), check_same_thread=False))
    saver.setup()
    return saver


def build_graph(deps: GraphDeps, checkpointer: SqliteSaver) -> Any:
    """Compile the ledger-only query graph with the given checkpointer."""
    nodes = {
        "route": route,
        "bound": bound,
        "resolve_entities": resolve_entities,
        "retrieve": retrieve,
        "gate": gate,
        "synthesize": synthesize,
        "verify": verify,
    }
    graph = StateGraph(QueryState)
    for name, node in nodes.items():
        graph.add_node(name, lambda state, node=node: node(state, deps))

    graph.add_edge(START, "route")
    graph.add_edge("route", "bound")
    graph.add_conditional_edges(
        "bound", _continue_unless_refused("resolve_entities"), ["resolve_entities", END]
    )
    graph.add_edge("resolve_entities", "retrieve")
    graph.add_edge("retrieve", "gate")
    graph.add_conditional_edges("gate", _continue_unless_refused("synthesize"), ["synthesize", END])
    graph.add_edge("synthesize", "verify")
    graph.add_edge("verify", END)
    return graph.compile(checkpointer=checkpointer)


def thread_novel(checkpointer: SqliteSaver, thread_id: str) -> Optional[str]:
    """Return the novel a thread is pinned to, or None for a new thread."""
    saved = checkpointer.get_tuple({"configurable": {"thread_id": thread_id}})
    if saved is None:
        return None
    return saved.checkpoint["channel_values"].get("novel_id")


def ask(
    graph: Any, *, thread_id: str, novel_id: str, query: str, until: Optional[int] = None
) -> QueryState:
    """Run one turn; chapter_max is left out so a follow-up inherits it from the thread."""
    return graph.invoke(
        {
            "novel_id": novel_id,
            "thread_id": thread_id,
            "query": query,
            "until": until,
            "mode": "compact",
        },
        {"configurable": {"thread_id": thread_id}},
    )
