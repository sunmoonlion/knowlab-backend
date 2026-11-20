"""Recursive structural planning does not certify admission or grant resources."""

from uuid import uuid4

import pytest

from app.domain.knowbots.formal_method import FormalMethodDefinition
from app.domain.knowbots.formal_method_plan import expand_unique_plan
from tests.test_formal_method import composite, frozen, resolved, tool


def leaf():
    raw = tool()
    raw["procedure"]["implementation"]["kind"] = "mcp"
    return frozen(raw)


def expert(*children, mid=None, version=1, **limits):
    raw = composite(*children)
    raw["procedure"].update(max_replans=0, max_steps=1000)
    raw["procedure"].update(limits)
    for step in raw["procedure"]["steps"]:
        step["on_failure"] = "fail"
    return frozen(raw, mid=mid, version=version)


def expanded(root, *dependencies):
    return expand_unique_plan(
        root.reference.method_id, root.definition, resolved(*dependencies)
    )


def test_nested_paths_classification_and_per_invocation_counts():
    base = leaf()
    child = expert(base, base)
    root = expert(child, child, base)
    plan = expanded(root, base, child)
    assert plan.method_kind == "expert"
    assert (plan.depth, plan.expansions, plan.steps) == (3, 8, 7)
    assert [n.path for n in plan.nodes] == [
        ("s0",),
        ("s0", "s0"),
        ("s0", "s1"),
        ("s1",),
        ("s1", "s0"),
        ("s1", "s1"),
        ("s2",),
    ]
    assert len(plan.leaf_nodes) == 5 and len(plan.dependencies) == 2
    assert all(n.parent_path == n.path[:-1] for n in plan.nodes)


def test_same_expert_id_versions_are_one_kind_but_two_expert_ids_form_team():
    base = leaf()
    a = expert(base)
    a2 = expert(base, mid=a.reference.method_id, version=2)
    b = expert(base)
    assert expanded(expert(a, a2), base, a, a2).method_kind == "expert"
    team = expert(a, b)
    assert expanded(team, base, a, b).method_kind == "expert_team"
    assert expanded(expert(team), base, a, b, team).method_kind == "expert_team"


@pytest.mark.parametrize(
    "limits", [{"max_depth": 2}, {"max_expansions": 3}, {"max_steps": 2}]
)
def test_whole_subtree_must_fit_root_limits(limits):
    base = leaf()
    child = expert(base, base)
    with pytest.raises(ValueError, match="limits"):
        expanded(expert(child, **limits), base, child)


def test_child_limit_is_not_overridden_by_larger_root():
    base = leaf()
    child = expert(base, base, max_steps=1)
    with pytest.raises(ValueError, match="limits"):
        expanded(expert(child), base, child)


def test_100_leaf_ceiling_counts_repeated_refs_across_branches():
    base = leaf()
    child = expert(*([base] * 10))
    assert len(expanded(expert(*([child] * 10)), base, child).leaf_nodes) == 100
    with pytest.raises(ValueError, match="100 actual tool"):
        expanded(expert(*([child] * 11)), base, child)


def test_missing_changed_and_ancestor_identity_dependencies_are_rejected():
    base = leaf()
    root = expert(base)
    with pytest.raises(ValueError, match="dependency"):
        expanded(root)
    other = leaf()
    with pytest.raises(ValueError, match="dependency"):
        expand_unique_plan(
            root.reference.method_id,
            root.definition,
            {(base.reference.method_id, 1): other},
        )
    with pytest.raises(ValueError, match="cycle"):
        expand_unique_plan(base.reference.method_id, root.definition, resolved(base))


def test_global_depth_and_expansion_caps_cannot_be_reset_by_children():
    base = leaf()
    chain = [base]
    for _ in range(15):
        chain.append(expert(chain[-1]))
    assert expanded(chain[-1], *chain[:-1]).depth == 16
    with pytest.raises(ValueError, match="limit"):
        expanded(expert(chain[-1]), *chain)
    child = expert(*([base] * 100))
    with pytest.raises(ValueError, match="limit"):
        expanded(expert(*([child] * 10)), base, child)


def test_topology_and_closure_are_canonical_without_changing_definition_digest():
    base = leaf()
    child = expert(base)
    root = expert(child, base)
    raw = root.definition.document()
    raw["procedure"]["steps"].reverse()
    reordered = FormalMethodDefinition.model_validate(raw)
    assert reordered.digest() != root.definition.digest()
    assert expand_unique_plan(
        root.reference.method_id, reordered, resolved(child, base)
    ) == expanded(root, base, child)
    assert (
        expand_unique_plan(uuid4(), reordered, resolved(child, base)).dependencies
        == expanded(root, child, base).dependencies
    )


def test_platform_and_internal_model_leaf_are_not_supported():
    platform = frozen()
    with pytest.raises(ValueError, match="direct MCP"):
        expanded(expert(platform), platform)


def test_mixed_plan_retains_each_frozen_connector_and_script_invocation():
    connector = leaf()
    raw = tool()
    raw["procedure"]["implementation"].update(kind="skill_script", entry="calculate.py")
    script = frozen(raw)
    plan = expanded(expert(connector, script, connector), connector, script)
    assert [node.reference for node in plan.leaf_nodes] == [
        connector.reference,
        script.reference,
        connector.reference,
    ]
    assert len(plan.dependencies) == 2
