"""Evaluate the actual workflow policy; scheduler replay is a model, not live GH.

The fixture contains real REST fields and logged reusable inputs. GitHub does not
return the original nested webhook or a cancellation initiator, so replaying the
suspected build-completion event cannot prove historical causation.
"""

import ast
import copy
import json
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
PUBLISH = ROOT / ".github/workflows/publish-images.yml"
FIXTURE = ROOT / "backend/tests/fixtures/publish_cancellation_20261024.json"
OLD_GROUP = "publish-images-${{ inputs.candidate_branch || github.event.workflow_run.head_branch }}"


def evaluate(expression, context):
    """Interpret only the Actions expression subset used here; no Python eval.

    Missing properties are empty strings, &&/|| return operands, and string
    equality is case-insensitive, as in Actions. Unknown syntax fails the test.
    """
    expression = expression.replace("&&", " and ").replace("||", " or ")

    def visit(node):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            return context[node.id]
        if isinstance(node, ast.Attribute):
            value = visit(node.value)
            return value.get(node.attr, "") if isinstance(value, dict) else ""
        if isinstance(node, ast.BoolOp):
            value = visit(node.values[0])
            for operand in node.values[1:]:
                if (isinstance(node.op, ast.And) and value) or (
                    isinstance(node.op, ast.Or) and not value
                ):
                    value = visit(operand)
            return value
        if isinstance(node, ast.Compare):
            assert len(node.ops) == 1 and isinstance(node.ops[0], ast.Eq)
            left, right = visit(node.left), visit(node.comparators[0])
            if isinstance(left, str) and isinstance(right, str):
                return left.casefold() == right.casefold()
            return left == right
        if isinstance(node, ast.Call):
            assert isinstance(node.func, ast.Name) and node.func.id == "format"
            assert not node.keywords
            template, *args = [visit(arg) for arg in node.args]
            return template.format(*args)
        pytest.fail(f"Unsupported expression syntax: {ast.dump(node)}")

    return visit(ast.parse(f"({expression.strip()})", mode="eval").body)


def group(workflow, context):
    return re.sub(
        r"\$\{\{(.*?)\}\}",
        lambda match: str(evaluate(match[1], context)),
        workflow["concurrency"]["group"],
        flags=re.DOTALL,
    ).casefold()


def cancels(workflow, active, incoming):
    # GitHub workflow-level groups compete before job-level skips can protect
    # the active run. Also covers pending replacement, not just running jobs.
    return workflow["concurrency"]["cancel-in-progress"] is True and group(
        workflow, active
    ) == group(workflow, incoming)


@pytest.fixture
def workflow():
    return yaml.safe_load(PUBLISH.read_text())


@pytest.fixture
def recorded():
    return json.loads(FIXTURE.read_text())


def dev_call(recorded, run_id=None):
    run = recorded["tests"]
    return {
        "inputs": recorded["inputs"].copy(),
        "github": {
            "event_name": run["event"],  # reusable sees push, not workflow_call
            "run_id": run_id or run["id"],
            "event": {},
        },
    }


def completion(recorded, **changes):
    trigger = dict(recorded["build"], **changes)
    return {
        "inputs": {},
        "github": {
            "event_name": recorded["skipped"]["event"],
            "run_id": recorded["skipped"]["id"],
            "event": {"workflow_run": trigger},
        },
    }


def test_recorded_timeline_and_old_group_reproduce_collision(workflow, recorded):
    assert recorded["build"]["head_sha"] == recorded["inputs"]["candidate_sha"]
    assert recorded["authorizer"]["started_at"] < recorded["build"]["updated_at"]
    assert recorded["build"]["updated_at"] < recorded["skipped"]["created_at"]
    assert recorded["skipped"]["created_at"] < recorded["authorizer"]["completed_at"]
    assert recorded["authorizer"]["conclusion"] == "cancelled"
    assert recorded["skipped"]["conclusion"] == "skipped"
    old = copy.deepcopy(workflow)
    old["concurrency"]["group"] = OLD_GROUP
    active, inert = dev_call(recorded), completion(recorded)
    assert evaluate(workflow["jobs"]["authorize"]["if"], active)
    assert not evaluate(workflow["jobs"]["authorize"]["if"], inert)
    assert group(old, active) == "publish-images-dev"
    assert cancels(old, active, inert)
    assert not cancels(workflow, active, inert)


def test_dev_is_isolated_from_unchanged_default_branch_workflow(workflow, recorded):
    old = copy.deepcopy(workflow)
    old["concurrency"]["group"] = OLD_GROUP
    assert group(workflow, dev_call(recorded)) != group(old, completion(recorded))


@pytest.mark.parametrize("branch", ["dev", "main", "feature/example", ""])
@pytest.mark.parametrize("event", ["push", "pull_request", "workflow_dispatch"])
@pytest.mark.parametrize(
    "conclusion", ["success", "failure", "cancelled", "skipped", None]
)
def test_only_successful_main_push_completions_compete(
    workflow, recorded, branch, event, conclusion
):
    incoming = completion(
        recorded, head_branch=branch, event=event, conclusion=conclusion
    )
    eligible = branch == "main" and event == "push" and conclusion == "success"
    main = completion(recorded, head_branch="main")
    assert cancels(workflow, main, incoming) is eligible
    assert not cancels(workflow, dev_call(recorded), incoming)


def test_ignored_runs_do_not_replace_each_other_even_when_pending(workflow, recorded):
    first = completion(recorded)
    second = copy.deepcopy(first)
    second["github"]["run_id"] += 1
    assert not cancels(workflow, first, second)


def test_new_dev_candidate_still_supersedes_older_publisher(workflow, recorded):
    active = dev_call(recorded)
    newer = dev_call(recorded, run_id=40096441385)
    newer["inputs"]["candidate_sha"] = "f" * 40  # modeled newer candidate
    assert cancels(workflow, active, newer)
    assert group(workflow, active) != group(
        workflow, completion(recorded, head_branch="main")
    )


@pytest.mark.parametrize(
    "branch,attested", [("dev", False), ("main", True), ("feature/x", True), ("", True)]
)
def test_ineligible_reusable_inputs_cannot_cancel_dev(
    workflow, recorded, branch, attested
):
    incoming = dev_call(recorded, run_id=40096441385)
    incoming["inputs"].update(candidate_branch=branch, tests_attested=attested)
    assert not cancels(workflow, dev_call(recorded), incoming)


def test_workflow_run_filter_excludes_dev_without_disabling_main_or_reuse(workflow):
    # PyYAML's YAML 1.1 loader interprets the Actions key `on` as True.
    triggers = workflow[True]
    assert triggers["workflow_run"]["branches"] == ["main"]
    assert triggers["workflow_run"]["types"] == ["completed"]
    assert set(triggers["workflow_run"]["workflows"]) == {
        "Tests",
        "Build and Push Docker Image",
    }
    assert set(triggers["workflow_call"]["inputs"]) == {
        "candidate_sha",
        "candidate_branch",
        "tests_attested",
    }
