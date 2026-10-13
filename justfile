set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

# Build the installable plugin when invoked without a recipe.
default: package

# Build a Plugin Manager-installable ZIP from the current worktree.
package:
    python3 scripts/package_plugin.py

# Real Windows PyMOL, a temporary local SSH profile, fuzzy completion and Ctrl+click.
test-annotator-ssh host_python="/mnt/d/Softwares/Pymol3.1/python.exe":
    python3 tests/host/run_annotator_ssh_acceptance.py --python '{{host_python}}'

# Windows Qt + real temporary OpenSSH: only SSH progress, transfer/cache and cancellation.
test-ssh-ux host_python="/mnt/d/Softwares/Pymol3.1/pythonw.exe" output="output/13_ssh_ready_gui_20261002.json":
    python3 tests/host/run_annotator_ssh_acceptance.py --python '{{host_python}}' --output '{{output}}' --ssh-ux-only

# Read-only WAN acceptance through the existing user profile and login program.
test-ssh-real table profile="x" host_python="/mnt/d/Softwares/Pymol3.1/pythonw.exe" output="output/13_ssh_ready_real_20261002.json":
    script="$(wslpath -w "$PWD/tests/host/annotator_real_source_smoke.py")"; record="$PWD/{{output}}"; result="$(wslpath -w "$record")"; cd /mnt/c; '{{host_python}}' -I -u "$script" --table '{{table}}' --profile '{{profile}}' --output "$result"; python3 -c 'import json,sys; r=json.load(open(sys.argv[1],encoding="utf-8")); print(r["status"]); sys.exit(0 if r["status"] == "passed" else 1)' "$record"

# Real score rows/structures, overview, sampling, reference, save/cancel and compact UI.
test-review-overview table host_python="/mnt/d/Softwares/Pymol3.1/python.exe" output="output/01_sampling_reference_20260929.json":
    script="$(wslpath -w "$PWD/tests/host/01_sampling_reference_smoke.py")"; result="$(wslpath -w "$PWD/{{output}}")"; cd /mnt/c; '{{host_python}}' -I "$script" --table '{{table}}' --output "$result"

# Real Windows cmd.* gate, using bundled Python directly (no PowerShell policy changes).
test-host-commands host_python="/mnt/d/Softwares/Pymol3.1/python.exe" output="output/03_host_commands_20260929.json" checks="":
    script="$(wslpath -w "$PWD/tests/host/host_smoke.py")"; record="$PWD/{{output}}"; result="$(wslpath -w "$record")"; rm -f "$record"; cd /mnt/c; '{{host_python}}' -I -u -c 'import os, runpy, sys; os.environ["PP_HOST_SMOKE_OUTPUT"] = sys.argv[2]; os.environ["PP_HOST_SMOKE_CHECK_WORKER"] = "0"; os.environ["PP_HOST_SMOKE_CHECKS"] = sys.argv[3]; os.environ["PP_HOST_SMOKE_GUI"] = "1"; runpy.run_path(sys.argv[1], run_name="__main__")' "$script" "$result" '{{checks}}'; python3 -c 'import json, sys; result = json.load(open(sys.argv[1], encoding="utf-8")); assert result["schema"] == "pymolplugins.host-smoke/1.0"; print("automatic_status=" + result["automatic_status"]); sys.exit(0 if result["automatic_status"] == "pass" else 1)' "$record"

# Install the specified release ZIP in a temporary Plugin Manager directory.
test-package package="dist/PymolPlugins-0.16.0.zip" host_python="/mnt/d/Softwares/Pymol3.1/python.exe" output="output/plugin_package.json":
    script="$(wslpath -w "$PWD/tests/host/plugin_package_smoke.py")"; archive="$(wslpath -w "$PWD/{{package}}")"; result="$(wslpath -w "$PWD/{{output}}")"; cd /mnt/c; '{{host_python}}' -I "$script" "$archive" --output "$result"

# Installed desktop UI: quiet defaults, on-demand help, first import and Save and next.
test-onboarding package="dist/PymolPlugins-0.16.0.zip" host_python="/mnt/d/Softwares/Pymol3.1/python.exe" output="output/16_filter_groups_20261002.json":
    script="$(wslpath -w "$PWD/tests/host/plugin_package_smoke.py")"; archive="$(wslpath -w "$PWD/{{package}}")"; record="$PWD/{{output}}"; result="$(wslpath -w "$record")"; cd /mnt/c; '{{host_python}}' -I "$script" "$archive" --output "$result" --onboarding-only; python3 -c 'import json,sys; r=json.load(open(sys.argv[1],encoding="utf-8")); print(r["status"]); sys.exit(0 if r["status"] == "passed" else 1)' "$record"

# Current provider + real Windows GUI command generation/edit/execute acceptance.
test-ai-commands host_python="/mnt/d/Softwares/Pymol3.1/pythonw.exe" output="output/15_command_ux_20261002.json" package="" preset_inputs="" stage="all" preset="":
    script="$(wslpath -w "$PWD/tests/host/02_ai_commands_smoke.py")"; evidence="$(wslpath -w "$PWD/output/01_sampling_reference_20260929.json")"; record="$PWD/{{output}}"; result="$(wslpath -w "$record")"; extra=(); if [ -n '{{package}}' ]; then extra=(--package "$(wslpath -w "$PWD/{{package}}")"); fi; if [ -n '{{preset_inputs}}' ]; then extra+=(--preset-inputs "$(wslpath -w "$PWD/{{preset_inputs}}")"); fi; cd /mnt/c; '{{host_python}}' -I -u "$script" --source-evidence "$evidence" --output "$result" --stage '{{stage}}' --preset '{{preset}}' "${extra[@]}"; python3 -c 'import json,sys; r=json.load(open(sys.argv[1],encoding="utf-8")); print(r["status"]); sys.exit(0 if r["status"] == "passed" else 1)' "$record"

# Remove generated package artifacts.
clean-package:
    rm -rf dist

# Public structures for native PPI/PLI/oligomer/membrane preset acceptance.
fetch-preset-inputs:
    curl --noproxy "*" -fsSL --connect-timeout 15 --max-time 60 https://files.rcsb.org/download/1BRS.pdb -o output/15_preset_1brs.pdb
    curl --noproxy "*" -fsSL --connect-timeout 15 --max-time 60 https://files.rcsb.org/download/1A4W.pdb -o output/15_preset_1a4w.pdb
    curl --noproxy "*" -fsSL --connect-timeout 15 --max-time 60 https://files.rcsb.org/download/2RH1.pdb -o output/15_preset_2rh1.pdb
