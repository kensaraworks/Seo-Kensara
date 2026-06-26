import re
import argparse
import importlib
from pathlib import Path

def md_to_docx(md_path: Path, docx_path: Path):
    try:
        docx = importlib.import_module("docx")
    except ModuleNotFoundError:
        print("Missing dependency: python-docx")
        print("Install it with: pip install python-docx")
        return

    doc = docx.Document()
    
    with md_path.open('r', encoding='utf-8') as f:
        content = f.read()
        
    for line in content.split('\n'):
        line = line.strip()
        if not line:
            continue
            
        if line.startswith('# '):
            doc.add_heading(line[2:], level=1)
        elif line.startswith('## '):
            doc.add_heading(line[3:], level=2)
        elif line.startswith('### '):
            doc.add_heading(line[4:], level=3)
        elif line.startswith('- **'):
            p = doc.add_paragraph(style='List Bullet')
            # Extract bold part
            match = re.match(r'- \*\*(.*?)\*\*(.*)', line)
            if match:
                bold_text = match.group(1)
                rest = match.group(2)
                p.add_run(bold_text).bold = True
                p.add_run(rest)
            else:
                p.add_run(line[2:])
        elif line.startswith('**'):
            p = doc.add_paragraph()
            p.add_run(line.strip('*')).bold = True
        elif line == '---':
            pass
        else:
            doc.add_paragraph(line)

    docx_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(docx_path)
    print("Saved docx to", docx_path)

def _default_paths() -> tuple[Path, Path]:
    project_root = Path(__file__).resolve().parent
    input_md = project_root / "README.md"
    output_docx = project_root / "drafts" / "reports" / "README.docx"
    return input_md, output_docx


def main() -> None:
    default_input, default_output = _default_paths()
    parser = argparse.ArgumentParser(
        description="Convert a Markdown file into a DOCX file.",
    )
    parser.add_argument(
        "input_md",
        nargs="?",
        default=str(default_input),
        help=f"Input Markdown file path (default: {default_input})",
    )
    parser.add_argument(
        "output_docx",
        nargs="?",
        default=str(default_output),
        help=f"Output DOCX file path (default: {default_output})",
    )
    args = parser.parse_args()

    md_file = Path(args.input_md)
    docx_file = Path(args.output_docx)

    if not md_file.is_absolute():
        md_file = Path(__file__).resolve().parent / md_file
    if not docx_file.is_absolute():
        docx_file = Path(__file__).resolve().parent / docx_file

    if not md_file.exists():
        print(f"Input Markdown file not found: {md_file}")
        print("Pass an explicit path, e.g.:")
        print("  python convert_docs.py docs/your-file.md drafts/reports/your-file.docx")
        return

    md_to_docx(md_file, docx_file)


if __name__ == "__main__":
    main()
