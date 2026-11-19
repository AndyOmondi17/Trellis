# Trellis GitHub Publishing Directives & Preparation

> **IMPORTANT CONSTRAINT:**
> **DO NOT DELETE any directories or files locally** (do not delete `os-taxonomy/`, `algebrica/`, or existing extraction scripts from the filesystem).
> Instead, ensure that Git completely ignores and stops tracking them, and that zero references, strings, or identifiers related to legacy reference datasets exist in any file tracked by Git.

---

### Step 1: Git Ignore Exclusions (Applied)
The root [`.gitignore`](.gitignore) excludes all external datasets, local databases, environments, and intermediate extraction artifacts:

```gitignore
# Bytecode & Caches
__pycache__/
*.py[cod]
*$py.class
.pytest_cache/

# Local Environment & Secrets
.env
.env.*
*.log

# Databases & Benchmarks
*.db
*.sqlite3
scripts/benchmark_results.json

# Keep locally, never upload to Git
os-taxonomy/
algebrica/
data/clean_spec/raw_concept_inventory.json
scripts/extract_naked_concepts.py
scripts/audit_ip_compliance.py

# Python Virtual Environments & IDE
.venv/
venv/
.idea/
.vscode/
```

---

### Step 2: Tracked File Clean-Room Verification
Prior to initializing or pushing to GitHub:
1. Verify that `.gitignore` prevents staging of ignored files:
   `git status` or `git check-ignore -v <path>`
2. Verify zero legacy strings in tracked source, schema, or test files:
   `git grep -i "algebrica"`
   `git grep -i "os-taxonomy"`
3. Confirm that all local reference folders (`os-taxonomy/`, `algebrica/`) remain intact on the local filesystem.
