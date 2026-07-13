# Contributing

Contributions are welcome when they preserve the canonical single-file RAPP
cartridge and ROM-free public boundary.

1. Fork and create a focused branch.
2. Keep the real capability in `agent.py`; helpers may bootstrap or launch it
   but must not replace its native manifest, metadata, or `perform` contract.
3. Add deterministic tests with mocks or generated in-memory bytes. Never add a
   ROM, save, screenshot, clip, runtime dump, credential, or personal path.
4. Run:

   ```bash
   ruff check .
   python -m compileall -q agent.py src tests scripts
   pytest
   bash -n bootstrap.sh launch.sh uninstall.sh
   python -m build
   python scripts/check_public_artifacts.py
   ```

5. Explain privacy/security impact and RAPP compatibility in the pull request.

Do not use a real ROM in CI or attach one to an issue. Keep Copilot calls, GUIs,
and credentials out of tests.
