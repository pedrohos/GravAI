import ast
import json
from pathlib import Path


def format_and_save_single_segment(file_path: Path, content: str | list | dict) -> Path:
    """
    Writes the segments as JSON and returns the path actually written.
    """
    # Content may already be parsed data (e.g. passed in-process straight from
    # the whisper response) or the string repr of it read back from a file -
    # only the latter needs literal-eval parsing.
    if isinstance(content, str):
        try:
            segments = ast.literal_eval(content)
        except (ValueError, SyntaxError) as e:
            raise ValueError(f"Failed to parse {file_path.name}: {e}")
    else:
        segments = content

    # Write as formatted JSON
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(segments, f, indent=2, ensure_ascii=False)

    output_path = file_path.with_suffix('.json')
    file_path.rename(output_path)

    print(f"{output_path.name} ({len(segments)} segments)")
    return output_path


def format_transcription_segments(str_path: str) -> None:
    
    path = Path(str_path)
    
    if not path.exists():
        raise FileNotFoundError(f"Path not found: {path}")
    
    # If it's a file, process it
    if path.is_file():
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
            format_and_save_single_segment(path, content)
    
    # If it's a directory, process all transcription segment files
    elif path.is_dir():
        segment_files = sorted(path.glob("*_transcription_segments.txt"))
        if segment_files:
            for file in segment_files:
                with open(file, 'r', encoding='utf-8') as f:
                    content = f.read()
                format_and_save_single_segment(file, content)
            print(f"Formatted {len(segment_files)} file(s)")
    else:
        raise ValueError(f"Path is neither a file nor a directory: {path}")