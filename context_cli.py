import os
import sys
from collections import defaultdict
import tiktoken
import pathspec
import questionary

DEFAULT_IGNORE_DIRS = {'.git', 'node_modules', '__pycache__', '.venv', 'venv', 'dist', 'build'}
IGNORE_EXTS = {
    '.png', '.jpg', '.jpeg', '.gif', '.ico', '.svg', '.pdf',
    '.zip', '.tar', '.gz', '.exe', '.dll', '.so', '.pyc', '.lock'
}

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

            tokens = len(enc.encode(content, disallowed_special=()))
            dir_key = os.path.dirname(rel_path)  # "" для корня проекта
            files_data.append({
                "dir": dir_key,
                "rel_path": rel_path,
                "abs_path": filepath,
                "tokens": tokens,
                "content": content,
            })

    return files_data

def main():
    # 1. Выбор папки проекта
    target_dir = questionary.path(
        "Выберите папку проекта:",
        default=".",
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

    # Группируем файлы по папкам
    dirs_map = defaultdict(list)
    for f in files_data:
        dirs_map[f["dir"]].append(f)

    total_all = sum(f["tokens"] for f in files_data)
    print(f"Найдено папок: {len(dirs_map)} | Файлов: {len(files_data)} | Всего токенов: {total_all:,}\n")

    # 2. Выбор папок целиком
    dir_choices = []
    for dir_key in sorted(dirs_map.keys()):
        dir_files = dirs_map[dir_key]
        dir_tokens = sum(f["tokens"] for f in dir_files)
        display_name = "./ (корень проекта)" if dir_key == "" else f"{dir_key}/"
        dir_choices.append(
            questionary.Choice(
                title=f"[{dir_tokens:>7,} tok | {len(dir_files):>3} файл.] {display_name}",
                value=dir_key,
                checked=True
            )
        )

    selected_dirs = questionary.checkbox(
        "Шаг 1. Выберите нужные папки (Пробел — вкл/выкл, 'a' — все, Enter — далее):",
        choices=dir_choices
    ).ask()

    if not selected_dirs:
        print("Ни одна папка не выбрана. Выход.")
        sys.exit(0)

    # Собираем файлы из выбранных папок
    selected_files = [f for d in selected_dirs for f in dirs_map[d]]
    dir_tokens_total = sum(f["tokens"] for f in selected_files)
    print(f"\nВ выбранных папках: {len(selected_files)} файлов (~{dir_tokens_total:,} токенов)")

    # 3. Опциональное уточнение по отдельным файлам
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

    # 4. Итоговая статистика
    final_tokens = sum(f["tokens"] for f in selected_files)
    print(f"\nИтого выбрано файлов: {len(selected_files)}")
    print(f"Итого токенов (~): {final_tokens:,}\n")

    # 5. Сохранение результата
    if questionary.confirm("Сохранить выбранные файлы в один текстовый файл?", default=True).ask():
        output_file = questionary.text(
            "Имя выходного файла:",
            default="context.txt"
        ).ask()

        if output_file:
            chunks = [
                f'<file path="{f["rel_path"]}">\n{f["content"]}\n</file>\n'
                for f in selected_files
            ]
            with open(output_file, 'w', encoding='utf-8') as out:
                out.write("\n".join(chunks))
            print(f"\nГотово! Контекст сохранен в {os.path.abspath(output_file)}")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nВыход.")