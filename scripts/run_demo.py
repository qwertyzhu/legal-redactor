"""Generate fictional fixtures and run ai + production demos."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from legal_redactor.formats import docx_io, pdf_io, text_io  # noqa: E402
from legal_redactor.pipeline import redact_file  # noqa: E402

# Import sample text
sys.path.insert(0, str(ROOT / "examples" / "fictional"))
from sample_contract_text import FICTIONAL_CONTRACT  # noqa: E402


def clean_demo_output(output_dir: Path, expected: Path) -> None:
    if output_dir != expected:
        raise ValueError("--clean is limited to repository demo-output")
    if output_dir.exists():
        if output_dir.is_symlink() or getattr(output_dir, "is_junction", lambda: False)():
            raise ValueError("--clean refuses symlink/junction")
        shutil.rmtree(output_dir)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "demo-output")
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()

    output_dir = Path(os.path.abspath(os.fspath(args.output_dir)))
    default = Path(os.path.abspath(os.fspath(ROOT / "demo-output")))
    if args.clean:
        clean_demo_output(output_dir, default)

    fixtures = output_dir / "fixtures"
    fixtures.mkdir(parents=True, exist_ok=True)

    txt = fixtures / "sample_contract.md"
    docx = fixtures / "sample_contract.docx"
    pdf = fixtures / "sample_contract.pdf"
    text_io.redact_text  # silence linters about import
    txt.write_text(FICTIONAL_CONTRACT, encoding="utf-8")
    docx_io.create_sample_docx(docx, FICTIONAL_CONTRACT)
    pdf_io.create_sample_pdf(pdf, FICTIONAL_CONTRACT)

    entities_ai = ROOT / "examples" / "fictional" / "entities_ai.json"
    entities_prod = ROOT / "examples" / "fictional" / "entities_production.json"

    results = []
    for mode, entities in (("ai", entities_ai), ("production", entities_prod)):
        for src in (txt, docx, pdf):
            out = output_dir / mode / f"{src.stem}.redacted{src.suffix}"
            result = redact_file(
                input_path=src,
                mode=mode,
                output_path=out,
                entities_path=entities,
                work_dir=output_dir / mode,
            )
            status = "PASS" if result.ok else "FAIL"
            print(f"[{status}] mode={mode} {src.name} -> {out.name} replaced={len(result.plan.entities)}")
            if not result.ok:
                print(f"         {result.residual.summary}")
            results.append(result)

    failed = [r for r in results if not r.ok]
    if failed:
        print(f"\n{len(failed)} run(s) failed residual scan", file=sys.stderr)
        return 2
    print("\nAll demo redactions passed residual scan.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
