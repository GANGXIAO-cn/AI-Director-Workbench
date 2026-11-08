"""Run pinned test assertions against both the original validators and workbench receipt inputs."""
import ast
from contextlib import ExitStack
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from historical_baseline import PIN, ROOT, SKILL

sys.path[:0] = [str(ROOT/"server"), str(SKILL/"tests")]
from workbench.archivechecks import assess, character_contract
from workbench.archivefiles import ArchiveFiles, capture
from workbench.errors import Rejected
from workbench import imageinventory, contactsheet, depictions, promptreceipt, readable, review_records, rhythm, specs, shotrefs, sceneprovenance, promptsignals
from workbench.storage import Store


def run(directory):
    subprocess.run(["git","-C",SKILL,"diff","--quiet",PIN,"--","scripts","tests"],check=True)
    import test_h3_rules
    import test_aspect_ratio
    import test_scene_anchors
    import test_m2_m6
    import test_character_contact
    import test_character_contract
    import test_reference_depictions
    import test_flat_timeline
    import test_prompt_review
    import test_quality_boundaries
    import test_shot_references
    import test_shot_rhythm
    store = Store(directory/"receipts")

    def archive(root, data):
        hashes, meta = capture(root, {"episode.json":json.dumps(data).encode()},store,[],root,{})
        return ArchiveFiles(hashes,meta,store)

    def contract(root, data):
        return character_contract(archive(root,data), data)

    def check(root, data):
        return depictions.check_depictions(archive(root,data), data)

    def anchors(root, data, *args):
        return depictions.check_anchors(archive(root,data), data, *args)

    def review_call(fn, manifest):
        data = json.loads(manifest.read_text())
        copies = archive(manifest.parent, data)
        return fn(data, copies.read, copies.identity_bytes)

    def report(root, data):
        return rhythm.packaged_report(data, archive(root, data).text)

    def flat_text(segment, text, headings, flat=False, h3_rules=False):
        assert flat
        return readable.check_flat_text(text, segment["reference_strategy"], segment["duration_s"]*1000,
                                        segment["id"], h3_rules=h3_rules)

    project_production = json.loads((ROOT/"configs/projects/puggy.json").read_text())["production"]
    def baseline(data, root):
        hashes, meta = capture(root, {"episode.json":json.dumps(data).encode()},store,[],root,project_production)
        files = ArchiveFiles(hashes,meta,store)
        return sceneprovenance.baseline_warnings(data,meta["sceneBaselineRule"],files.scene_registry)

    def contact(manifest, output):
        data = json.loads(manifest.read_bytes())
        root = manifest.parent
        roles = getattr(case,"lock",{}).get("assets",[])
        try:
            hashes, meta = capture(root, {"episode.json":manifest.read_bytes()},store,roles,root,project_production)
            result = contactsheet.build(data, ArchiveFiles(hashes,meta,store), meta["contactRules"])
            contactsheet.export(result,store,output)
        except Rejected as exc:
            # The source command exposes these same input failures as ValueError.
            raise ValueError(str(exc)) from exc
        return result

    # The source cases below assert only geometry. Compare those assertions to
    # the dedicated aspect result exposed on a Workbench historical receipt.
    def aspect_result(root, data):
        result = assess(archive(root, data), data)["aspect"]
        return result["state"] == "passed", result["message"]

    def aspect_validator(fixture):
        from test_support import complete
        complete(fixture.root, fixture.data)
        ok, diagnosis = aspect_result(fixture.root, fixture.data)
        return subprocess.CompletedProcess(["workbench", "historical_receipt", "aspect"], 0 if ok else 1, "", diagnosis)

    def aspect_manifest(manifest):
        ok, _ = aspect_result(manifest.parent, json.loads(manifest.read_bytes()))
        return {"ok": ok}

    def signal_warnings(root, data):
        # Resolve the prescribed test standard including the original fixture's local override.
        standard = test_quality_boundaries.rp.standard_file()
        config = {"characterStandard":standard.name, "promptStandard":"视频提示词标准.json"}
        hashes, meta = capture(root, {"episode.json":json.dumps(data).encode()},store,[],standard.parent,config)
        return promptsignals.archived(ArchiveFiles(hashes,meta,store),data)["warnings"]

    def image_copy(root):
        raw = (root/"episode.json").read_bytes()
        hashes,meta = capture(root,{"episode.json":raw},store,[],root,{},include_image_inventory=True)
        return ArchiveFiles(hashes,meta,store), json.loads(raw)

    def images(root):
        return imageinventory.inventory(*image_copy(root))

    def image_state(root):
        copies, data = image_copy(root)
        details = imageinventory.inventory(copies,data)
        used,segments = imageinventory.usage(copies,data)
        ledger = dict(schema_version=1,episode_root=str(root),images=details["images"])
        return ledger,imageinventory.groups(copies)[0],used,segments,root/"episode.json"

    inventory_entries = {"ims.scan":images,"ims.state":image_state,
        "ims.usage":lambda root:imageinventory.usage(*image_copy(root)),
        "ims.inventory_details":lambda root:imageinventory.groups(image_copy(root)[0]),
        "ims.inventory":lambda root:imageinventory.groups(image_copy(root)[0])[0]}

    adapters = {test_h3_rules: {"check_h3_sound": readable.check_h3_sound}, test_quality_boundaries: {"rp.episode_warnings":signal_warnings,
                    "rp.check_text": lambda seg,text,headings:readable.check_timeline_text(text,seg["reference_strategy"],
                            round(seg["duration_s"]*1000),seg["id"],flat=False)}, test_character_contact: {"cs.build":contact}, test_scene_anchors: {"cs.build":contact, "bp.scene_anchor_warnings":lambda data, root:
                    sceneprovenance.scene_anchor_warnings(data,archive(root,data).scene_bytes)},
                test_m2_m6: {"baseline_warnings":baseline},test_shot_references: {
                    "check_shot_references":lambda root,data:shotrefs.check_shot_references(archive(root,data).text,data),
                    "suggestions":lambda root,data:shotrefs.suggestions(archive(root,data).text,data)},
                test_aspect_ratio: {"ve.aspect_matches": specs.aspect_matches,
                                    "legacy.ValidatorIntegration.run_validator": aspect_validator, "ve.validate": aspect_manifest}, test_shot_rhythm: {"analyse": report},
                test_flat_timeline: {"analyse": report, "check_text":flat_text, "passages": lambda value, duration, sid:
                                     readable.flat_windows(value, duration * 1000, sid)},
                test_prompt_review: {"pr.prepare": lambda manifest:review_call(promptreceipt.pending_record, manifest),
                                     "pr.verify": lambda manifest:review_call(promptreceipt.verify, manifest),
                                     "records": review_records.records},test_character_contract: {"check_contract":contract,"reviewed_moments":depictions.reviewed_moments,
                                           "check_anchors":anchors},
                test_reference_depictions: {"check_depictions":check}}
    cli_cases = {"test_snapshot_option_removed", "test_valid_new_grid_with_actual_neutral_anchor_in_normal_validator"}
    selected = {test_h3_rules: {"test_silence_and_input_types"}, test_shot_references: {"test_last_shot_reaction_only","test_full_box_on_neutral_board",
                    "test_combined_identity_board_requires_separate_extra_use","test_reject_invalid_shots_and_missing_identity_coverage",
                    "test_reject_board_cast_mismatch_and_conflict","test_same_shot_continuation_emits_one_id",
                    "test_known_role_and_actual_visual_evidence_required"},
                test_aspect_ratio: {"test_requested_ratios", "test_invalid_ratio_rejected", "test_grid_must_match_target",
                                    "test_equivalent_ratios_accepted_in_settings", "test_conflicting_settings_blocked",
                                    "test_flex_reference_keeps_independent_shape",
                                    "test_zero_image_dimension_fails_without_division","test_rounding_tolerance"},
                test_flat_timeline: {"test_counts_and_shared_parser","test_timing_and_markers", "test_nested_seconds_scope",
                                    "test_rows_and_viewing_tasks", "test_conflicts_and_review"},
                test_prompt_review: {"test_capture_before_review_and_unchanged_pass", "test_changed_between_submission_and_approval_rejected",
                                     "test_review_material_requires_existing_sources", "test_single_segment_update_leaves_other_record_identical",
                                     "test_images_replace_reorder_add_remove", "test_unrelated_sources_or_heading_order_do_not_expire",
                                     "test_table_unordered_columns", "test_missing_cold_compare_scope_conflict_unresolved",
                                     "test_duplicate_verdict_rejected", "test_conflict_after_blank_line_and_duplicate_table_columns_rejected",
                                     "test_observation_before_table_and_unrelated_prose", "test_fields_can_have_blank_lines_and_multiline_observation"}}
    paths = subprocess.check_output(["git","-C",SKILL,"ls-tree","-r","--name-only",PIN,"--","tests"]).decode().splitlines()
    inventory = []
    for path in paths:
        raw = subprocess.check_output(["git","-C",SKILL,"show",PIN+":"+path])
        records = []
        for cls in ast.parse(raw).body:
            if isinstance(cls,ast.ClassDef):
                records.extend(dict(name=f"{cls.name}.{fn.name}", line=fn.lineno, status="待逐例迁移")
                               for fn in cls.body if isinstance(fn,ast.FunctionDef) and fn.name.startswith("test_"))
        inventory.append(dict(source=path, sha256=sha256(raw).hexdigest(), cases=records))
    for module, replacements in adapters.items():
        file = next(row for row in inventory if row["source"]=="tests/"+module.__name__+".py")
        for entry in file["cases"]:
            cls, method = entry["name"].split(".")
            if module is test_quality_boundaries and cls not in ("PromptSignalsTests","InventoryBoundaryTests"):
                continue
            if module is test_scene_anchors and cls not in ("AnchorTests","ContactSheetTests") or module is test_m2_m6 and cls != "BaselineTests":
                continue
            if module in selected and method not in selected[module]:
                continue
            if method in cli_cases:
                entry["reason"] = "原用例通过子进程执行 CLI，需另接工作台完整入口"
                continue
            test = getattr(module,cls)
            for mode in ("source", "workbench"):
                calls = []
                boundary = module is test_quality_boundaries and cls=="InventoryBoundaryTests"
                depth = 0
                def traced(name, fn):
                    def call(*args, **kwargs):
                        nonlocal depth
                        if not boundary or depth==0:
                            calls.append(name)
                        depth += 1
                        try:
                            return fn(*args, **kwargs)
                        finally:
                            depth -= 1
                    return call
                with ExitStack() as stack:
                    for name, native in (inventory_entries if boundary else replacements).items():
                        if module is test_aspect_ratio and name == "ve.aspect_matches" and method not in (
                                "test_zero_image_dimension_fails_without_division", "test_rounding_tolerance"):
                            continue  # Count validator entry calls, not nested source helpers.
                        owner_name, _, attr = name.rpartition(".")
                        owner = module
                        for part in owner_name.split(".") if owner_name else ():
                            owner = getattr(owner,part)
                        fn = getattr(owner,attr) if mode=="source" else native
                        stack.enter_context(patch.object(owner,attr,traced(name,fn)))
                    result = unittest.TestResult()
                    case = test(method)
                    case.run(result)
                    assert result.wasSuccessful() and calls, (entry,mode,result.failures,result.errors)
                entry[mode+"Calls"] = calls
            assert entry["sourceCalls"] == entry["workbenchCalls"], entry
            entry["status"] = "同一用例原断言在工作台副本上通过"
            if boundary:
                entry["scope"] = "原技能盘点入口与历史回执候选图片冻结、重算入口；记录每次顶层调用"
            if module is test_aspect_ratio and method not in ("test_zero_image_dimension_fails_without_division", "test_rounding_tolerance"):
                entry["scope"] = "原清单验证入口与工作台历史回执的画幅专项，比较该用例的画幅断言"

    from image_choice_cases import choice_cases, detail_case, extra_candidate_case
    import test_image_choices
    by_file = {f["source"]: f["cases"] for f in inventory}
    from page_reference_cases import receipt_cases
    receipt_cases(directory/"page-references",by_file)
    choice_cases(directory/"image-choices", by_file["tests/test_image_choices.py"], test_image_choices)
    extra = next(c for c in by_file["tests/test_image_choices.py"] if "full_delivery_without_ledger" in c["name"])
    extra_candidate_case(directory/"extra-candidate", extra, test_image_choices)
    detail = next(c for c in by_file["tests/test_quality_boundaries.py"] if c["name"].startswith("DetailPageTests."))
    detail_case(directory/"detail-page", detail, test_quality_boundaries)
    cases = [c for f in inventory for c in f["cases"]]
    count = sum(c["status"].endswith("通过") for c in cases)
    return dict(sourceCommit=PIN, sourceFiles=len(inventory), declaredCases=len(cases), adaptedCases=count, files=inventory)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="adw-source-cases-") as tmp:
        result = run(Path(tmp))
    Path(sys.argv[1]).write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n")
    print({k:v for k,v in result.items() if k!="files"})
