"""
main.py — CLI entry point for the Codebase AI Tool.

Run with:  python main.py
"""

import os
import sys
from pathlib import Path

# ── Bootstrap: install dependencies if missing ────────────────────────────────
def _ensure_deps():
    import importlib, subprocess
    packages = {
        "rich": "rich",
        "requests": "requests",
        "dotenv": "python-dotenv",
    }
    missing = []
    for mod, pkg in packages.items():
        try:
            importlib.import_module(mod)
        except ImportError:
            missing.append(pkg)
    if missing:
        print(f"[setup] Installing missing packages: {', '.join(missing)} …")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", *missing])
        print("[setup] Done. Continuing …\n")

_ensure_deps()

# ── Imports (after deps are ensured) ─────────────────────────────────────────
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt, Confirm
from rich.syntax import Syntax
from rich.rule import Rule
from rich.text import Text
from rich import box

import db
import indexer
import ai_chat
import patcher

console = Console()

BANNER = """
[bold cyan]╔══════════════════════════════════════════╗[/]
[bold cyan]║[/]  [bold white]🧠  Codebase AI Tool[/]  [bold cyan]║[/]
[bold cyan]╚══════════════════════════════════════════╝[/]
[dim]Powered by NVIDIA Qwen 3.5[/]
"""


# ─── Pretty helpers ──────────────────────────────────────────────────────────

def clear():
    os.system("cls" if os.name == "nt" else "clear")


def print_menu():
    console.print(BANNER)
    table = Table(box=box.ROUNDED, show_header=False, border_style="cyan")
    table.add_column("Key", style="bold yellow", width=4)
    table.add_column("Action", style="white")
    table.add_row("1", "➕  Add / re-index a codebase")
    table.add_row("2", "💬  Chat with a codebase (AI diff mode)")
    table.add_row("3", "🔧  (Coming soon)")
    table.add_row("4", "🚪  Exit")
    console.print(table)
    console.print()


def print_codebases(codebases: list[dict]) -> None:
    if not codebases:
        console.print("[yellow]No codebases indexed yet. Choose option 1 first.[/]")
        return
    table = Table(title="Indexed Codebases", box=box.SIMPLE_HEAD, border_style="cyan")
    table.add_column("#",    style="bold yellow", width=4, justify="right")
    table.add_column("Name", style="bold white")
    table.add_column("Path", style="dim")
    table.add_column("Added", style="dim", width=20)
    for i, cb in enumerate(codebases, 1):
        table.add_row(str(i), cb["name"], cb["path"], cb["added_at"][:19])
    console.print(table)


# ─── Option 1: Add / re-index a codebase ─────────────────────────────────────

def option_add_codebase():
    console.print(Rule("[bold cyan]Add / Re-index Codebase[/]"))

    path_input = Prompt.ask("[cyan]Enter the full path to the codebase folder[/]").strip()
    root_path = str(Path(path_input).resolve())

    if not os.path.isdir(root_path):
        console.print(f"[red]✗  Directory not found:[/] {root_path}")
        return

    name = Prompt.ask(
        "[cyan]Give this codebase a name[/]",
        default=Path(root_path).name,
    ).strip()

    with console.status("[cyan]Indexing codebase — please wait …[/]", spinner="dots"):
        codebase_id = db.add_codebase(name, root_path)
        file_count, _ = indexer.index_codebase(codebase_id, root_path)

    console.print(
        Panel(
            f"[green]✔  Indexed [bold]{file_count}[/] files[/]\n"
            f"[dim]Codebase:[/] [white]{name}[/]\n"
            f"[dim]Path:[/]     [white]{root_path}[/]",
            title="[bold green]Success[/]",
            border_style="green",
        )
    )
    Prompt.ask("\n[dim]Press Enter to return to menu[/]", default="")


# ─── Option 2: Chat with a codebase ──────────────────────────────────────────

def _pick_codebase() -> dict | None:
    codebases = db.get_all_codebases()
    print_codebases(codebases)
    if not codebases:
        return None

    console.print()
    choice = Prompt.ask(
        "[cyan]Enter codebase number[/] [dim](or 'q' to cancel)[/]",
        default="q",
    ).strip()

    if choice.lower() == "q":
        return None

    try:
        idx = int(choice) - 1
        if 0 <= idx < len(codebases):
            return codebases[idx]
    except ValueError:
        pass

    console.print("[red]Invalid selection.[/]")
    return None


def _display_diff(diff_text: str):
    if diff_text.strip():
        console.print()
        console.print(Rule("[bold yellow]Generated Diff[/]"))
        syntax = Syntax(diff_text, "diff", theme="monokai", line_numbers=True)
        console.print(syntax)
        console.print()
    else:
        console.print("[yellow]No diff found in response.[/]")


def option_chat():
    console.print(Rule("[bold cyan]Chat with a Codebase[/]"))

    if not ai_chat.check_api_key():
        console.print(
            Panel(
                "[red]NVIDIA_API_KEY not set.[/]\n\n"
                "Create a [bold].env[/] file in the tool's directory with:\n"
                "[cyan]NVIDIA_API_KEY=your_key_here[/]",
                title="[bold red]Missing API Key[/]",
                border_style="red",
            )
        )
        Prompt.ask("\n[dim]Press Enter to return to menu[/]", default="")
        return

    cb = _pick_codebase()
    if cb is None:
        return

    codebase_id: int = cb["id"]
    root_path: str = cb["path"]

    console.print(
        Panel(
            f"[green]Connected to:[/] [bold white]{cb['name']}[/]\n"
            f"[dim]{root_path}[/]\n\n"
            "[dim]Commands: [bold]clear[/] = reset history  |  [bold]quit[/] = back to menu[/]",
            title="[bold cyan]Chat Session[/]",
            border_style="cyan",
        )
    )

    while True:
        console.print()
        user_input = Prompt.ask("[bold green]You[/]").strip()

        if not user_input:
            continue

        if user_input.lower() in ("quit", "exit", "q"):
            console.print("[dim]Returning to menu …[/]")
            break

        if user_input.lower() == "clear":
            db.clear_chat_history(codebase_id)
            console.print("[yellow]Chat history cleared.[/]")
            continue

        # ── Stream AI response ─────────────────────────────────────────────
        console.print()
        console.print("[bold magenta]AI[/] ", end="")

        full_response = ""
        try:
            def on_chunk(text: str):
                nonlocal full_response
                full_response += text
                console.print(text, end="", markup=False, highlight=False)

            full_response = ai_chat.chat_with_ai(
                codebase_id=codebase_id,
                user_message=user_input,
                on_chunk=on_chunk,
            )
            console.print()  # newline after stream ends
        except Exception as e:
            console.print(f"\n[red]AI error:[/] {e}")
            continue

        # ── Extract & display diff ─────────────────────────────────────────
        diff_text = patcher.extract_diff_from_response(full_response)

        if diff_text:
            _display_diff(diff_text)

            apply = Confirm.ask(
                "[bold yellow]Apply this diff to the codebase?[/]",
                default=False,
            )

            if apply:
                with console.status("[cyan]Applying patch …[/]", spinner="point"):
                    results = patcher.apply_diff_to_codebase(diff_text, root_path, codebase_id)

                console.print()
                for ok, msg in results:
                    if ok:
                        console.print(f"  [green]✔[/] {msg}")
                    else:
                        console.print(f"  [red]✗[/] {msg}")
                console.print()
        else:
            # Pure text response — no diff
            console.print("[dim](No diff in response — no code changes to apply.)[/]")


# ─── Option 3: Placeholder ────────────────────────────────────────────────────

def option_coming_soon():
    console.print(
        Panel(
            "[yellow]This feature is coming soon! 🚧[/]",
            title="[bold yellow]Option 3[/]",
            border_style="yellow",
        )
    )
    Prompt.ask("\n[dim]Press Enter to return to menu[/]", default="")


# ─── Main loop ────────────────────────────────────────────────────────────────

def main():
    db.init_db()

    while True:
        clear()
        print_menu()

        choice = Prompt.ask(
            "[bold cyan]Choose an option[/]",
            choices=["1", "2", "3", "4"],
            show_choices=False,
        ).strip()

        clear()

        if choice == "1":
            option_add_codebase()
        elif choice == "2":
            option_chat()
        elif choice == "3":
            option_coming_soon()
        elif choice == "4":
            console.print("\n[bold cyan]Goodbye! 👋[/]\n")
            sys.exit(0)


if __name__ == "__main__":
    main()
