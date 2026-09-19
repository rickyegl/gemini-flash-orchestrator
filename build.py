"""Package this skill as dist/gemini-flash-orchestrator.skill (a zip).

The archive holds one top-level folder named after the skill, containing
SKILL.md and scripts/, which is the layout skill importers expect.
"""

import os
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
NAME = "gemini-flash-orchestrator"
INCLUDE = ["SKILL.md", "scripts"]


def main() -> None:
  out_dir = os.path.join(ROOT, "dist")
  os.makedirs(out_dir, exist_ok=True)
  out = os.path.join(out_dir, f"{NAME}.skill")
  with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
    for item in INCLUDE:
      path = os.path.join(ROOT, item)
      if os.path.isfile(path):
        zf.write(path, f"{NAME}/{item}")
        continue
      for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in filenames:
          if fn.endswith(".pyc"):
            continue
          full = os.path.join(dirpath, fn)
          rel = os.path.relpath(full, ROOT).replace(os.sep, "/")
          zf.write(full, f"{NAME}/{rel}")
  print(out)


if __name__ == "__main__":
  main()
