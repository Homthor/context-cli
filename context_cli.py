import os
import sys
from collections import defaultdict
import tiktoken
import pathspec
import questionary

DEFAULT_IGNORE_DIRS = {'.git', 'node_modules', '__pycache__', '.venv', 'venv', 'dist', 'build'}
IGNORE_FILES = {'context.txt'}
IGNORE_EXTS = {
    '.png', '.jpg', '.jpeg', '.gif', '.ico', '.svg', '.pdf',
    '.zip', '.tar', '.gz', '.exe', '.dll', '.so', '.pyc', '.lock'
}
GENERATED_MARKER = "<!-- ctx-generated -->"


def load_gitignore(root_dir: str) -> pathspec.PathSpec | None:
    gitignore_path = os.path.join(root_dir, '.gitignore')
    if not os.path.exists(gitignore_path):
        return None
    with open(gitignore_path, 'r', encoding='utf-8') as f:
        return pathspec.PathSpec.from_lines('gitwildmatch', f)


def scan_files(root_dir: str, encoding_name: str = "o200k_base"):
    root_dir = os.path.abspath(root_dir)
    enc = tiktoken.get_encoding(encoding_name)
    spec = load_gitignore(root_dir)
    files_data = []

    for dirpath, dirnames, filenames in os.walk(root_dir):
        rel_dir = os.path.relpath(dirpath, root_dir)
        rel_dir = "" if rel_dir == "." else rel_dir

        filtered_dirs = []
        for d in dirnames:
            if d in DEFAULT_IGNORE_DIRS:
                continue
            rel_path = os.path.join(rel_dir, d).replace(os.sep, '/') + '/'
            if spec and spec.match_file(rel_path):
                continue
            filtered_dirs.append(d)
        dirnames[:] = sorted(filtered_dirs)

        for filename in sorted(filenames):
            if filename in IGNORE_FILES:
                continue

            if any(filename.lower().endswith(ext) for ext in IGNORE_EXTS):
                continue

            rel_path = os.path.join(rel_dir, filename).replace(os.sep, '/')
            if spec and spec.match_file(rel_path):
                continue

            filepath = os.path.join(dirpath, filename)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read()
            except (UnicodeDecodeError, PermissionError, OSError):
                continue

            # Пропускаем файлы, ранее сгенерированные этим скриптом (независимо от их имени)
            if content.startswith(GENERATED_MARKER):
                continue

            tokens = len(enc.encode(content, disallowed_special=()))
            dir_key = os.path.dirname(rel_path)
            files_data.append({
                "dir": dir_key,
                "rel_path": rel_path,
                "abs_path": filepath,
                "tokens": tokens,
                "content": content,
            })

    return files_data


def get_dir_ancestors(dir_key: str) -> list[str]:
    if not dir_key:
        return [""]
    parts = dir_key.split('/')
    return ['/'.join(parts[:i]) for i in range(1, len(parts) + 1)]


def is_file_included(file_dir: str, selected_dirs: set[str]) -> bool:
    if file_dir == "":
        return "" in selected_dirs
    return all(ancestor in selected_dirs for ancestor in get_dir_ancestors(file_dir))


def main():
    default_dir = sys.argv[1] if len(sys.argv) > 1 else "."
    target_dir = questionary.path(
        "Выберите папку проекта (нажмите Enter для текущей):",
        default=default_dir,
        only_directories=True
    ).ask()

    if not target_dir or not os.path.isdir(target_dir):
        print("Отменено или папка не найдена.")
        sys.exit(0)

    print("\nСканирование файлов и подсчет токенов...")
    files_data = scan_files(target_dir)

    if not files_data:
        print("Подходящих текстовых файлов не найдено.")
        sys.exit(0)

    all_dirs = set()
    direct_files_map = defaultdict(list)

    for f in files_data:
        direct_files_map[f["dir"]].append(f)
        for anc in get_dir_ancestors(f["dir"]):
            all_dirs.add(anc)

    total_all = sum(f["tokens"] for f in files_data)
    print(f"Найдено папок: {len(all_dirs)} | Файлов: {len(files_data)} | Всего токенов: {total_all:,}\n")

    dir_choices = []
    for dir_key in sorted(all_dirs):
        if dir_key == "":
            branch_files = direct_files_map[""]
            display_name = "./ (только файлы в корне)"
        else:
            prefix = dir_key + "/"
            branch_files = [
                f for f in files_data
                if f["dir"] == dir_key or f["dir"].startswith(prefix)
            ]
            depth = dir_key.count('/')
            indent = "  " * depth
            display_name = f"{indent}{dir_key}/"

        branch_tokens = sum(f["tokens"] for f in branch_files)
        dir_choices.append(
            questionary.Choice(
                title=f"[{branch_tokens:>7,} tok | {len(branch_files):>3} файл.] {display_name}",
                value=dir_key,
                checked=True
            )
        )

    selected_dirs_list = questionary.checkbox(
        "Шаг 1. Выберите нужные папки (снятие родителя исключит его подпапки):",
        choices=dir_choices
    ).ask()

    if not selected_dirs_list:
        print("Ни одна папка не выбрана. Выход.")
        sys.exit(0)

    selected_dirs = set(selected_dirs_list)
    selected_files = [
        f for f in files_data
        if is_file_included(f["dir"], selected_dirs)
    ]

    if not selected_files:
        print("После исключения родительских папок не осталось ни одного файла. Выход.")
        sys.exit(0)

    dir_tokens_total = sum(f["tokens"] for f in selected_files)
    print(f"\nВ выбранных ветках: {len(selected_files)} файлов (~{dir_tokens_total:,} токенов)")

    if questionary.confirm("Уточнить список отдельных файлов внутри выбранных папок?", default=False).ask():
        file_choices = [
            questionary.Choice(
                title=f"[{f['tokens']:>7,} tok] {f['rel_path']}",
                value=f,
                checked=True
            )
            for f in selected_files
        ]
        selected_files = questionary.checkbox(
            "Шаг 2. Отметьте нужные файлы (Пробел — вкл/выкл, 'a' — все, Enter — готово):",
            choices=file_choices
        ).ask()

        if not selected_files:
            print("Ни один файл не выбран. Выход.")
            sys.exit(0)

    final_tokens = sum(f["tokens"] for f in selected_files)
    print(f"\nИтого выбрано файлов: {len(selected_files)}")
    print(f"Итого токенов (~): {final_tokens:,}\n")

    if questionary.confirm("Сохранить выбранные файлы в один текстовый файл?", default=True).ask():
        output_file = questionary.text(
            "Имя выходного файла:",
            default="context.txt"
        ).ask()

        if output_file:
            output_abs = os.path.abspath(output_file)
            chunks = [
                f'<file path="{f["rel_path"]}">\n{f["content"]}\n</file>\n'
                for f in selected_files
                if f["abs_path"] != output_abs
            ]
            with open(output_file, 'w', encoding='utf-8') as out:
                out.write(f"{GENERATED_MARKER}\n" + "\n".join(chunks))
            print(f"\nГотово! Контекст сохранен в {output_abs}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nВыход.")