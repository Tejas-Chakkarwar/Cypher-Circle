from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar

from rich.console import Console
from rich.markup import escape as _rich_escape
from rich.panel import Panel
from rich.table import Table

from .auth_service import AuthService
from .config import load_settings
from .db import Database, LoggedQuery, QueryLog
from .errors import (
    AlreadyFollowingError,
    AuthenticationError,
    ConflictError,
    CypherCircleError,
    DataIntegrityError,
    DatabaseUnavailableError,
    MembershipError,
    NotFollowingError,
    NotFoundError,
    QueryError,
    SelfActionError,
    StartupDataError,
    ValidationError,
    VisualizationError,
)
from .graph_service import GraphService
from .models import (
    CircleSummary,
    Connection,
    GraphStats,
    MutualConnections,
    Profile,
    Recommendation,
    Session,
)
from .profile_service import ProfileService
from .search_service import SearchService
from .startup_service import StartupService
from .viz_service import VisualizationService


InputFunc = Callable[[str], str]
PasswordFunc = Callable[[str], str]
ResultT = TypeVar("ResultT")

LOGGER = logging.getLogger("cyphercircle.cli")
_ANSI_OR_OSC_RE = re.compile(
    r"\x1b\][^\x07]*(?:\x07|\x1b\\)"
    r"|\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])"
    r"|\x9d[^\x07\x9c]*(?:\x07|\x9c)"
    r"|\x9b[0-?]*[ -/]*[@-~]"
)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def escape_markup(value: object, *, preserve_newlines: bool = False) -> str:
    """Remove terminal controls before escaping Rich markup."""

    text = str(value)
    text = _ANSI_OR_OSC_RE.sub("", text)
    text = _CONTROL_RE.sub("", text)
    if not preserve_newlines:
        text = re.sub(r"[\r\n\u2028\u2029]", " ", text)
    return _rich_escape(text)


class CLI:
    """Rich console interface over the shared service layer."""

    def __init__(
        self,
        auth: AuthService,
        profile: ProfileService,
        graph: GraphService,
        search: SearchService,
        query_log: QueryLog,
        *,
        console: Console | None = None,
        input_func: InputFunc | None = None,
        password_func: PasswordFunc | None = None,
        logger: logging.Logger | None = None,
        startup: StartupService | None = None,
        stats: GraphStats | None = None,
        visualization: VisualizationService | None = None,
    ) -> None:
        self.auth = auth
        self.profile = profile
        self.graph = graph
        self.search = search
        self.query_log = query_log
        self.console = console or Console()
        self.input_func = input_func or self.console.input
        self.password_func = password_func or self._console_password
        self.logger = logger or LOGGER
        self.startup = startup
        self.stats = stats
        self.visualization = visualization
        self._display_entries: list[LoggedQuery] | None = None
        self.session: Session | None = None
        self.show_cypher = False

    def run(self) -> int:
        """Run the start menu until the user logs out or exits."""

        while True:
            try:
                self._start_banner()
                choice = self._ask(
                    "[1] Login  [2] Register  [0] Exit\n"
                    "Choose"
                ).strip().lower()
            except KeyboardInterrupt:
                self.console.print("\n[dim]Goodbye.[/dim]")
                return 0

            if choice == "1":
                session = self._execute("UC-2", self._login)
            elif choice == "2":
                session = self._execute("UC-1", self._register)
            elif choice == "0":
                self.console.print("[dim]Goodbye.[/dim]")
                return 0
            else:
                self.console.print("[yellow]That is not a valid menu choice. Choose 1, 2, or 0.[/yellow]")
                continue

            if session is None:
                continue
            self.session = session
            outcome = self._main_menu()
            if outcome == "exit":
                self.session = None
                return 0
            self.session = None

    def _main_menu(self) -> str:
        while self.session is not None:
            try:
                self._header()
                choice = self._ask(
                    "[1] View profile  [2] Edit profile  [3] Follow  [4] Unfollow\n"
                    "[5] Connections  [6] Mutual connections  [7] Recommendations\n"
                    "[8] Search users  [9] Popular users  [V] Network visualization\n"
                    "[C] Toggle Show Cypher  [0] Logout  [X] Exit\n"
                    "Choose"
                ).strip().lower()
            except KeyboardInterrupt:
                self.console.print("\n[yellow]Logging out.[/yellow]")
                return "logout"

            actions: dict[str, tuple[str, Callable[[], Any]]] = {
                "1": ("UC-3", self._view_profile),
                "3": ("UC-5", self._follow),
                "4": ("UC-6", self._unfollow),
                "5": ("UC-7", self._connections),
                "6": ("UC-8", self._mutual_connections),
                "7": ("UC-9", self._recommendations),
                "8": ("UC-10", self._search_users),
                "9": ("UC-11", self._popular_users),
            }
            if choice == "2":
                self._edit_profile()
            elif choice in actions:
                label, action = actions[choice]
                self._execute(label, action)
            elif choice == "v":
                self._execute("UC-12", self._visualization)
            elif choice == "c":
                self.show_cypher = not self.show_cypher
                state = "ON" if self.show_cypher else "OFF"
                self.console.print(f"[green]Show Cypher is now {state}.[/green]")
            elif choice == "0":
                self.console.print("[dim]Logged out.[/dim]")
                return "logout"
            elif choice == "x":
                self.console.print("[dim]Goodbye.[/dim]")
                return "exit"
            else:
                self.console.print("[yellow]That is not a valid menu choice.[/yellow]")
        return "logout"

    def _register(self) -> Session | None:
        self.console.print(Panel("[bold]UC-1 · Register[/bold]", border_style="cyan"))
        name = self._ask("Name").strip()
        email = self._ask("Email").strip()
        username = self._ask("Username").strip()
        password = self._password("Password")
        confirmation = self._password("Confirm password")
        if password != confirmation:
            raise ValidationError("passwords do not match")
        bio_input = self._ask("Bio (optional; press Enter to skip)")
        bio = bio_input if bio_input else None
        circles = self.profile.get_top_circles(10)
        selected = self._select_circles(circles)
        session = self.auth.register(
            name,
            email,
            username,
            password,
            bio=bio,
            circle_ids=selected,
        )
        self.console.print(
            f"[green]Account created for @{escape_markup(session.username)}.[/green]"
        )
        return session

    def _login(self) -> Session | None:
        self.console.print(Panel("[bold]UC-2 · Login[/bold]", border_style="cyan"))
        username = self._ask("Username").strip()
        for attempt in range(3):
            password = self._password("Password")
            try:
                session = self.auth.login(username, password)
            except (AuthenticationError, ValidationError):
                remaining = 2 - attempt
                self.console.print("[red]Invalid username or password.[/red]")
                if remaining:
                    self.console.print(f"[yellow]{remaining} attempt(s) remaining.[/yellow]")
                    continue
                self.console.print("[yellow]Too many failed attempts. Returning to the start menu.[/yellow]")
                return None
            self.console.print(f"[green]Welcome back, {escape_markup(session.name)}.[/green]")
            return session
        return None

    def _view_profile(self) -> None:
        session = self._require_session()
        profile = self.profile.get_profile(session)
        self._print_profile(profile)

    def _edit_profile(self) -> None:
        session = self._require_session()
        try:
            while True:
                self._header()
                choice = self._ask(
                    "[bold]UC-4 · Edit profile[/bold]\n"
                    "[1] Edit details  [2] Join a circle  [3] Leave a circle  [0] Back\n"
                    "Choose"
                ).strip()
                if choice == "1":
                    self._execute("UC-4", lambda: self._edit_details(session))
                elif choice == "2":
                    self._execute("UC-4", lambda: self._join_circle(session))
                elif choice == "3":
                    self._execute("UC-4", lambda: self._leave_circle(session))
                elif choice == "0":
                    return
                else:
                    self.console.print("[yellow]Choose 1, 2, 3, or 0.[/yellow]")
        except KeyboardInterrupt:
            self.console.print("\n[yellow]Returning to the main menu.[/yellow]")

    def _edit_details(self, session: Session) -> None:
        self.console.print("[dim]Press Enter to keep a value. Type - to clear bio or location.[/dim]")
        name_input = self._ask("Name").strip()
        bio_input = self._ask("Bio").strip()
        location_input = self._ask("Location").strip()
        profile = self.profile.update_profile(
            session,
            name=name_input or None,
            bio=bio_input if bio_input else None,
            location=location_input if location_input else None,
        )
        self.console.print("[green]Profile updated.[/green]")
        self._print_profile(profile)

    def _join_circle(self, session: Session) -> None:
        circles = self.profile.get_top_circles(10)
        circle_id = self._select_circle(circles)
        if circle_id is None:
            return
        joined = self.profile.join_circle(session, circle_id)
        message = "You joined the circle." if joined else "You are already a member of that circle."
        self.console.print(f"[green]{message}[/green]" if joined else f"[yellow]{message}[/yellow]")

    def _leave_circle(self, session: Session) -> None:
        circles = self.profile.get_my_circles(session)
        circle_id = self._select_circle(circles)
        if circle_id is None:
            return
        self.profile.leave_circle(session, circle_id)
        self.console.print("[green]You left the circle.[/green]")

    def _follow(self) -> None:
        session = self._require_session()
        self.console.print(
            Panel("Follow another user", title="UC-5 · Follow a user", border_style="cyan")
        )
        target = self._ask("Username to follow").strip()
        self.graph.follow(session, target)
        self.console.print(
            f"[green]You are now following @{escape_markup(target.lower())}.[/green]"
        )

    def _unfollow(self) -> None:
        session = self._require_session()
        self.console.print(
            Panel(
                "Stop following another user",
                title="UC-6 · Unfollow a user",
                border_style="cyan",
            )
        )
        target = self._ask("Username to unfollow").strip()
        self.graph.unfollow(session, target)
        self.console.print(f"[green]You unfollowed @{escape_markup(target.lower())}.[/green]")

    def _connections(self) -> None:
        session = self._require_session()
        self.console.print(
            Panel(
                "Following and followers are shown separately.",
                title="UC-7 · Connections",
                border_style="cyan",
            )
        )
        page = 0
        page_size = 15
        while True:
            counts = self.graph.connection_counts(session)
            following = self.graph.following(session, skip=page * page_size, limit=page_size)
            followers = self.graph.followers(session, skip=page * page_size, limit=page_size)
            self._print_connections("Following", following, counts.following)
            self._print_connections("Followers", followers, counts.followers)
            command = self._ask("n next · p previous · b back").strip().lower()
            if command == "b":
                return
            if command == "n":
                if (page + 1) * page_size >= max(counts.following, counts.followers):
                    self.console.print("[yellow]You are on the last page.[/yellow]")
                else:
                    page += 1
            elif command == "p":
                if page == 0:
                    self.console.print("[yellow]You are on the first page.[/yellow]")
                else:
                    page -= 1
            else:
                self.console.print("[yellow]Use n, p, or b.[/yellow]")

    def _mutual_connections(self) -> None:
        session = self._require_session()
        other = self._ask("Username to compare").strip()
        page = 0
        page_size = 15
        while True:
            result = self.graph.mutual_connections(
                session, other, skip=page * page_size, limit=page_size
            )
            self._print_mutual(result, other, page, page_size)
            command = self._ask("n next · p previous · b back").strip().lower()
            if command == "b":
                return
            if command == "n":
                if (page + 1) * page_size >= result.total:
                    self.console.print("[yellow]You are on the last page.[/yellow]")
                else:
                    page += 1
            elif command == "p":
                if page == 0:
                    self.console.print("[yellow]You are on the first page.[/yellow]")
                else:
                    page -= 1
            else:
                self.console.print("[yellow]Use n, p, or b.[/yellow]")

    def _recommendations(self) -> None:
        session = self._require_session()
        recommendations = self.graph.recommendations(session, limit=10)
        if not recommendations:
            self.console.print("[yellow]No recommendations right now.[/yellow]")
            return
        table = Table(title="UC-9 · Friend Recommendations")
        table.add_column("#", justify="right")
        table.add_column("User")
        table.add_column("Why")
        table.add_column("Score", justify="right")
        for index, recommendation in enumerate(recommendations, 1):
            table.add_row(
                str(index),
                (
                    f"@{escape_markup(recommendation.user.username)} · "
                    f"{escape_markup(recommendation.user.name)}"
                ),
                self._recommendation_reason(recommendation),
                str(recommendation.score),
            )
        self.console.print(table)
        selection = self._ask("Follow one? Enter # or press Enter to skip").strip()
        if not selection:
            return
        index = self._selection_index(selection, len(recommendations))
        if index is None:
            self.console.print("[yellow]That selection is not valid.[/yellow]")
            return
        target = recommendations[index - 1].user.username
        self._run_mutation("UC-5", lambda: self.graph.follow(session, target))
        self.console.print(f"[green]You are now following @{escape_markup(target)}.[/green]")

    def _search_users(self) -> None:
        session = self._require_session()
        query = self._ask("Search (at least 2 characters)")
        results = self.search.search_users(session, query)
        if not results:
            self.console.print("[yellow]No users found.[/yellow]")
            return
        table = Table(title="UC-10 · Search Users")
        table.add_column("#", justify="right")
        table.add_column("User")
        table.add_column("Followers", justify="right")
        table.add_column("Following")
        for index, result in enumerate(results, 1):
            table.add_row(
                str(index),
                f"@{escape_markup(result.username)} · {escape_markup(result.name)}",
                str(result.followers),
                "✓" if result.i_follow else "",
            )
        self.console.print(table)
        selection = self._ask("Enter # to follow/unfollow, or Enter to go back").strip()
        if not selection:
            return
        index = self._selection_index(selection, len(results))
        if index is None:
            self.console.print("[yellow]That selection is not valid.[/yellow]")
            return
        result = results[index - 1]
        if result.i_follow:
            self._run_mutation("UC-6", lambda: self.graph.unfollow(session, result.username))
            self.console.print(
                f"[green]You unfollowed @{escape_markup(result.username)}.[/green]"
            )
        else:
            self._run_mutation("UC-5", lambda: self.graph.follow(session, result.username))
            self.console.print(
                f"[green]You are now following @{escape_markup(result.username)}.[/green]"
            )

    def _popular_users(self) -> None:
        session = self._require_session()
        users = self.search.popular_users(limit=10)
        if not users:
            self.console.print("[yellow]No popular users found.[/yellow]")
            return
        table = Table(title="UC-11 · Popular Users")
        table.add_column("#", justify="right")
        table.add_column("User")
        table.add_column("Followers", justify="right")
        table.add_column("Popularity")
        highest = max(user.followers for user in users) or 1
        for index, user in enumerate(users, 1):
            width = max(1, round(20 * user.followers / highest)) if user.followers else 0
            table.add_row(
                str(index),
                f"@{escape_markup(user.username)} · {escape_markup(user.name)}",
                str(user.followers),
                "█" * width,
            )
        self.console.print(table)
        selection = self._ask("Enter # to follow, or Enter to go back").strip()
        if not selection:
            return
        index = self._selection_index(selection, len(users))
        if index is None:
            self.console.print("[yellow]That selection is not valid.[/yellow]")
            return
        target = users[index - 1].username
        self._run_mutation("UC-5", lambda: self.graph.follow(session, target))
        self.console.print(f"[green]You are now following @{escape_markup(target)}.[/green]")

    def _visualization(self) -> None:
        session = self._require_session()
        if self.visualization is None:
            raise VisualizationError("Network visualization is not configured.")
        path = self.visualization.generate(session)
        if path is None:
            self.console.print(
                Panel(
                    "No network yet. Follow someone first.",
                    title="UC-12 · Network Visualization",
                    border_style="yellow",
                )
            )
            return
        self.console.print(
            Panel(
                f"Saved to {escape_markup(path)}\nThe visualization was opened in your browser.",
                title="UC-12 · Network Visualization",
                border_style="green",
            )
        )

    def _print_profile(self, profile: Profile) -> None:
        circles = list(profile.circles[:5])
        if profile.circle_count == 0:
            circle_text = "No circles yet"
        else:
            circle_text = ", ".join(escape_markup(circle) for circle in circles)
            extra = profile.circle_count - len(circles)
            if extra > 0:
                circle_text += f" (+{extra} more)"
        joined = self._format_datetime(profile.joined_at)
        body = (
            f"[bold]@{escape_markup(profile.username)}[/bold]\n"
            f"Name: {escape_markup(profile.name)}\n"
            f"Email: {escape_markup(profile.email)}\n"
            f"Bio: {escape_markup(profile.bio or '—')}\n"
            f"Location: {escape_markup(profile.location or '—')}\n"
            f"Circles: {circle_text}\n"
            f"Joined: {joined}\n"
            f"Following: {profile.following_count} · Followers: {profile.followers_count}"
        )
        self.console.print(Panel(body, title="UC-3 · Profile", border_style="green"))

    def _print_connections(
        self, title: str, connections: Iterable[Connection], total: int
    ) -> None:
        table = Table(title=f"{title} ({total})")
        table.add_column("User")
        table.add_column("Location")
        table.add_column("Since")
        table.add_column("Mutual")
        for connection in connections:
            table.add_row(
                f"@{escape_markup(connection.username)} · {escape_markup(connection.name)}",
                escape_markup(connection.location or "—"),
                self._format_datetime(connection.since),
                "↔" if connection.follows_back else "",
            )
        self.console.print(table)

    def _print_mutual(
        self, result: MutualConnections, other: str, page: int, page_size: int
    ) -> None:
        start = page * page_size + 1 if result.users else 0
        end = page * page_size + len(result.users)
        safe_other = escape_markup(other.lower())
        table = Table(title=f"UC-8 · Mutual Connections with @{safe_other}")
        table.add_column("User")
        table.add_column("Location")
        for user in result.users:
            table.add_row(
                f"@{escape_markup(user.username)} · {escape_markup(user.name)}",
                escape_markup(user.location or "—"),
            )
        self.console.print(
            Panel(
                f"You and @{safe_other} both follow {result.total} people "
                f"(showing {start}–{end}).\n{self._relationship_text(result)}",
                border_style="green",
            )
        )
        self.console.print(table)

    def _print_header(self) -> None:
        self._header()

    def _start_banner(self) -> None:
        stats = self._refresh_stats()
        if stats is None:
            self.console.print("[bold cyan]Cypher Circle[/bold cyan]")
            return
        self.console.print(
            Panel(
                f"{stats.users} users · {stats.follows} follows · {stats.circles} circles",
                title="Cypher Circle",
                border_style="cyan",
            )
        )

    def _header(self) -> None:
        if self.session is None:
            return
        state = "ON" if self.show_cypher else "OFF"
        stats = self._refresh_stats()
        stats_text = ""
        if stats is not None:
            stats_text = f" · {stats.users} users · {stats.follows} follows · {stats.circles} circles"
        self.console.print(
            f"[bold cyan]Cypher Circle[/bold cyan] · @{escape_markup(self.session.username)}"
            f"{stats_text} · "
            f"Show Cypher: {state}"
        )

    def _refresh_stats(self) -> GraphStats | None:
        if self.startup is not None:
            try:
                self.stats = self.startup.current_stats()
            except CypherCircleError:
                self.logger.exception("Could not refresh startup statistics")
        return self.stats

    def _show_queries(self) -> None:
        if not self.show_cypher:
            return
        entries = self._display_entries
        if entries is None:
            entries = self.query_log.entries()
        self._display_entries = None
        if not entries:
            self.console.print("[dim]No Cypher queries ran for this action.[/dim]")
            return
        for index, entry in enumerate(entries, 1):
            self.console.print(
                Panel(
                    self._query_text(entry),
                    title=f"Cypher {index}/{len(entries)} · {entry.name} · {entry.rows} rows",
                    border_style="blue",
                )
            )

    def _query_text(self, entry: LoggedQuery) -> str:
        query = entry.query.replace("password_hash", "[redacted-field]")
        params = json.dumps(entry.params, indent=2, sort_keys=True, default=str)
        params = params.replace("password_hash", "[redacted-field]")
        return (
            f"{escape_markup(query, preserve_newlines=True)}\n\n"
            f"Params:\n{escape_markup(params)}"
        )

    def _execute(self, label: str, action: Callable[[], ResultT]) -> ResultT | None:
        self._display_entries = None
        self.query_log.begin_action(label)
        try:
            return action()
        except KeyboardInterrupt:
            self.console.print("\n[yellow]Cancelled. Returning to the previous menu.[/yellow]")
            return None
        except (
            AlreadyFollowingError,
            AuthenticationError,
            ConflictError,
            DataIntegrityError,
            DatabaseUnavailableError,
            MembershipError,
            NotFollowingError,
            NotFoundError,
            QueryError,
            SelfActionError,
            ValidationError,
            VisualizationError,
        ) as exc:
            self._print_error(exc)
            return None
        except Exception:
            self.logger.exception("Unexpected CLI error during %s", label)
            self.console.print(
                "[red]Something unexpected happened. The error was logged to "
                "cyphercircle.log.[/red]"
            )
            return None
        finally:
            self._show_queries()

    def _run_mutation(self, label: str, operation: Callable[[], ResultT]) -> ResultT:
        previous_entries = self.query_log.entries()
        self.query_log.begin_action(label)
        try:
            return operation()
        finally:
            self._display_entries = previous_entries + self.query_log.entries()

    def _print_error(self, error: CypherCircleError) -> None:
        if isinstance(error, AuthenticationError):
            message = "Invalid username or password."
        elif isinstance(error, DatabaseUnavailableError):
            message = "Neo4j is unavailable. Is it running? Try `make up`."
        elif isinstance(error, DataIntegrityError):
            message = "The operation conflicts with existing data."
        elif isinstance(error, QueryError):
            message = "The database operation could not be completed."
        elif isinstance(error, VisualizationError):
            message = str(error)
        else:
            message = str(error)
        self.console.print(f"[red]{escape_markup(message)}[/red]")

    def _select_circles(self, circles: list[CircleSummary]) -> tuple[str, ...]:
        if not circles:
            return ()
        self._print_circles(circles)
        while True:
            raw = self._ask("Circle numbers (up to 3, comma-separated; Enter to skip)").strip()
            if not raw:
                return ()
            try:
                positions = self._parse_positions(raw, len(circles), maximum=3)
            except ValueError as exc:
                self.console.print(f"[yellow]{exc}[/yellow]")
                continue
            return tuple(circles[position - 1].circle_id for position in positions)

    def _select_circle(self, circles: list[CircleSummary]) -> str | None:
        if not circles:
            self.console.print("[yellow]No circles are available.[/yellow]")
            return None
        self._print_circles(circles)
        while True:
            raw = self._ask("Circle number (or Enter to go back)").strip()
            if not raw:
                return None
            try:
                position = self._parse_positions(raw, len(circles), maximum=1)[0]
            except ValueError as exc:
                self.console.print(f"[yellow]{exc}[/yellow]")
                continue
            return circles[position - 1].circle_id

    def _print_circles(self, circles: list[CircleSummary]) -> None:
        table = Table(title="Circles")
        table.add_column("#", justify="right")
        table.add_column("Circle")
        table.add_column("Members", justify="right")
        for index, circle in enumerate(circles, 1):
            table.add_row(str(index), escape_markup(circle.name), str(circle.members or "—"))
        self.console.print(table)

    @staticmethod
    def _parse_positions(raw: str, count: int, *, maximum: int) -> list[int]:
        values = [part.strip() for part in raw.split(",") if part.strip()]
        if not values:
            raise ValueError("Enter one or more numbers.")
        if len(values) > maximum:
            raise ValueError(f"Choose at most {maximum} item(s).")
        try:
            positions = [int(value) for value in values]
        except ValueError as exc:
            raise ValueError("Selections must be numbers.") from exc
        if len(set(positions)) != len(positions) or any(
            position < 1 or position > count for position in positions
        ):
            raise ValueError(f"Choose numbers from 1 to {count}.")
        return positions

    @staticmethod
    def _selection_index(raw: str, count: int) -> int | None:
        if not raw or not raw.isascii() or not raw.isdecimal():
            return None
        try:
            value = int(raw)
        except (ValueError, OverflowError):
            return None
        return value if 1 <= value <= count else None

    def _recommendation_reason(self, recommendation: Recommendation) -> str:
        if recommendation.reason == "network":
            via = ", ".join(f"@{escape_markup(username)}" for username in recommendation.via)
            return f"Network: followed by {via}" if via else "Network"
        if recommendation.reason == "circle":
            return f"Circle: {escape_markup(recommendation.circle or 'shared circle')}"
        return "Popular on Cypher Circle"

    @staticmethod
    def _relationship_text(result: MutualConnections) -> str:
        if result.status.i_follow and result.status.they_follow:
            return "You follow each other."
        if result.status.i_follow:
            return "You follow them."
        if result.status.they_follow:
            return "They follow you."
        return "No direct connection."

    def _require_session(self) -> Session:
        if self.session is None:
            raise AuthenticationError("please log in first")
        return self.session

    def _ask(self, prompt: str) -> str:
        try:
            return self.input_func(prompt)
        except EOFError as exc:
            raise KeyboardInterrupt from exc

    def _password(self, prompt: str) -> str:
        try:
            return self.password_func(prompt)
        except EOFError as exc:
            raise KeyboardInterrupt from exc

    def _console_password(self, prompt: str) -> str:
        return self.console.input(f"{prompt}: ", password=True)

    @staticmethod
    def _format_datetime(value: datetime) -> str:
        return value.isoformat().replace("+00:00", "Z")


def build_cli(console: Console | None = None) -> tuple[CLI, Database]:
    """Construct the CLI composition root without issuing application queries."""

    settings = load_settings()
    query_log = QueryLog()
    database = Database(settings, query_log)
    startup = StartupService(database)
    try:
        stats = startup.verify_ready()
    except Exception:
        database.close()
        raise
    return (
        CLI(
            AuthService(database),
            ProfileService(database),
            GraphService(database),
            SearchService(database),
            query_log,
            console=console,
            startup=startup,
            stats=stats,
            visualization=VisualizationService(database),
        ),
        database,
    )


def main() -> int:
    """Start the application and convert startup failures to friendly output."""

    logging.basicConfig(
        filename=Path("cyphercircle.log"),
        level=logging.ERROR,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    console = Console()
    database: Database | None = None
    try:
        cli, database = build_cli(console)
        return cli.run()
    except ValueError as exc:
        console.print(f"[red]Configuration error: {escape_markup(str(exc))}[/red]")
        return 1
    except DatabaseUnavailableError:
        console.print("[red]Neo4j is unavailable. Is it running? Try `make up`.[/red]")
        return 1
    except StartupDataError as exc:
        console.print(f"[red]{escape_markup(str(exc))}[/red]")
        return 1
    except CypherCircleError:
        console.print("[red]Cypher Circle could not start. Check the local configuration.[/red]")
        return 1
    except Exception:
        LOGGER.exception("Unexpected startup error")
        console.print("[red]Cypher Circle could not start. The error was logged to cyphercircle.log.[/red]")
        return 1
    finally:
        if database is not None:
            database.close()


CypherCircleCLI = CLI
