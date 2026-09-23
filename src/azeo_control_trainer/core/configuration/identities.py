"""Stable point identities shared by engineering metadata and released runtimes."""
from uuid import NAMESPACE_URL, uuid5


def point_identity(project, module_object, block, member, kind="terminal"):
    # Addresses are deliberately absent: changing a module/block label must
    # not split history, and a new object reusing that label must not inherit it.
    return str(uuid5(NAMESPACE_URL, "azeo-point:" + ":".join(
        str(value) for value in (project, module_object, block, kind, member))))


def graph_point_context(graph, block, member, kind="terminal"):
    source = getattr(graph, "_configuration_identity", None)
    if not source:
        return {}
    return {"point_id": point_identity(source["project_id"], source["object_id"], block.id, member, kind),
            "configuration": {**source, "block_id": block.id, "member": member, "kind": kind}}


def context_for_path(graphs, path):
    parts = path.split("/", 2)
    if len(parts) != 3:
        return {}
    module, name, member = parts
    graph = next((g for g in graphs if g.name == module), None)
    if graph is None:
        return {}
    block = next((b for b in graph.blocks.values() if (b.instance_name or b.id) == name), None)
    if block is None:
        return {}
    if member.startswith("CONFIG/"):
        parameter = member[7:]
        if parameter in block.config.params or parameter in block.get_config_schema():
            return graph_point_context(graph, block, parameter, "parameter")
    elif member in block.inputs or member in block.outputs:
        return graph_point_context(graph, block, member)
    return {}
