"""Flask browser interface for Cypher Circle.

The web layer is deliberately a thin adapter: authentication, validation,
authorization, graph behavior, and Cypher remain in the existing services.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import wraps
import hmac
import logging
from pathlib import Path
import secrets
from typing import Any, Callable, TypeVar
from urllib.parse import urlsplit

from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    session as flask_session,
    url_for,
)
from werkzeug.exceptions import HTTPException

from .auth_service import AuthService
from .config import WebSettings, load_settings, load_web_settings
from .db import Database, QueryLog
from .errors import (
    AuthenticationError,
    ConflictError,
    CypherCircleError,
    DataIntegrityError,
    DatabaseUnavailableError,
    NotFoundError,
    QueryError,
    StartupDataError,
    VisualizationError,
)
from .graph_service import MAX_SEPARATION_HOPS, GraphService
from .models import GraphStats, Session
from .profile_service import ProfileService
from .search_service import SearchService
from .startup_service import StartupService
from .viz_service import VisualizationService


LOGGER = logging.getLogger("cyphercircle.web")
View = TypeVar("View", bound=Callable[..., Any])


@dataclass(frozen=True)
class WebServices:
    auth: AuthService
    profile: ProfileService
    graph: GraphService
    search: SearchService
    startup: StartupService
    visualization: VisualizationService


def build_web_services() -> tuple[WebServices, Database]:
    """Construct the production web composition root."""

    database = Database(load_settings(), QueryLog())
    return (
        WebServices(
            auth=AuthService(database),
            profile=ProfileService(database),
            graph=GraphService(database),
            search=SearchService(database),
            startup=StartupService(database),
            # The web UI embeds the generated page in an iframe, so it must not
            # also launch a desktop browser tab on the machine running Flask.
            visualization=VisualizationService(database, browser_open=lambda _uri: True),
        ),
        database,
    )


def _safe_next(value: str | None) -> str | None:
    """Accept only local paths for post-action redirects."""

    if (
        not value
        or not value.startswith("/")
        or value.startswith("//")
        or "\\" in value
        or any(ord(character) < 0x20 for character in value)
    ):
        return None
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc:
        return None
    return value


def _friendly_error(error: CypherCircleError) -> str:
    if isinstance(error, AuthenticationError):
        return "Invalid username or password."
    if isinstance(error, DatabaseUnavailableError):
        return "Neo4j is unavailable. Is it running? Try `make up`."
    if isinstance(error, StartupDataError):
        return "The graph is not ready. Run `make data` and `make load`."
    if isinstance(error, ConflictError):
        # Conflict messages are written for users, e.g. "username is already taken".
        message = str(error)
        return message[:1].upper() + message[1:] + "."
    if isinstance(error, DataIntegrityError):
        return "That operation conflicts with existing data."
    if isinstance(error, QueryError):
        return "The database operation could not be completed."
    if isinstance(error, VisualizationError):
        return str(error)
    return str(error)


def _identity_session() -> Session | None:
    identity = flask_session.get("identity")
    if not isinstance(identity, dict):
        return None
    username = identity.get("username")
    name = identity.get("name")
    if not isinstance(username, str) or not isinstance(name, str):
        flask_session.pop("identity", None)
        return None
    return Session(username=username, name=name)


def _set_identity(identity: Session) -> None:
    flask_session.clear()
    flask_session["identity"] = {"username": identity.username, "name": identity.name}
    flask_session["_csrf_token"] = secrets.token_urlsafe(32)


def _csrf_token() -> str:
    token = flask_session.get("_csrf_token")
    if not isinstance(token, str) or not token:
        token = secrets.token_urlsafe(32)
        flask_session["_csrf_token"] = token
    return token


def _page(value: str | None) -> int:
    try:
        return max(1, min(int(value or "1"), 10_000))
    except (TypeError, ValueError):
        return 1


def login_required(view: View) -> View:
    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        if _identity_session() is None:
            flash("Please log in to continue.", "info")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped  # type: ignore[return-value]


def create_app(
    services: WebServices | None = None,
    *,
    web_settings: WebSettings | None = None,
    config: dict[str, Any] | None = None,
) -> Flask:
    """Create the Flask application, with injectable services for unit tests."""

    web_settings = web_settings or load_web_settings()
    app = Flask(
        __name__,
        template_folder="web_templates",
        static_folder="web_static",
    )
    app.config.from_mapping(
        SECRET_KEY=web_settings.secret_key,
        CSRF_ENABLED=True,
        WEB_DEBUG=web_settings.debug,
    )
    if config:
        app.config.update(config)

    database: Database | None = None
    if services is None:
        services, database = build_web_services()
        app.extensions["cyphercircle.database"] = database
    app.extensions["cyphercircle.services"] = services

    @app.context_processor
    def inject_shell() -> dict[str, Any]:
        return {
            "current_user": _identity_session(),
            "csrf_token": _csrf_token,
        }

    @app.before_request
    def protect_post_requests() -> None:
        if request.method != "POST" or not app.config.get("CSRF_ENABLED", True):
            return
        expected = flask_session.get("_csrf_token")
        submitted = request.form.get("csrf_token", "")
        if not isinstance(expected, str) or not isinstance(submitted, str):
            abort(400, description="Invalid or missing request token.")
        if not hmac.compare_digest(expected, submitted):
            abort(400, description="Invalid or missing request token.")

    @app.get("/")
    def index() -> str:
        stats: GraphStats | None = None
        stats_unavailable = False
        try:
            stats = services.startup.current_stats()
        except (CypherCircleError, ValueError):
            stats_unavailable = True
        return render_template("landing.html", stats=stats, stats_unavailable=stats_unavailable)

    @app.route("/register", methods=["GET", "POST"])
    def register() -> str | Any:
        circles = []
        try:
            circles = services.profile.get_top_circles(limit=10)
        except CypherCircleError:
            pass
        form = {
            "name": request.form.get("name", ""),
            "email": request.form.get("email", ""),
            "username": request.form.get("username", ""),
            "bio": request.form.get("bio", ""),
        }
        if request.method == "POST":
            password = request.form.get("password", "")
            if password != request.form.get("password_confirm", ""):
                return render_template(
                    "register.html",
                    form=form,
                    circles=circles,
                    error="Passwords do not match.",
                ), 400
            try:
                identity = services.auth.register(
                    form["name"],
                    form["email"],
                    form["username"],
                    password,
                    bio=form["bio"] or None,
                    circle_ids=request.form.getlist("circle_ids"),
                )
            except CypherCircleError as exc:
                return render_template(
                    "register.html", form=form, circles=circles, error=_friendly_error(exc)
                ), 400
            _set_identity(identity)
            flash(f"Welcome to Cypher Circle, {identity.name}.", "success")
            return redirect(url_for("dashboard"))
        return render_template("register.html", form=form, circles=circles, error=None)

    @app.route("/login", methods=["GET", "POST"])
    def login() -> str | Any:
        form = {"username": request.form.get("username", "")}
        next_url = _safe_next(request.form.get("next") or request.args.get("next"))
        if request.method == "POST":
            try:
                identity = services.auth.login(form["username"], request.form.get("password", ""))
            except CypherCircleError as exc:
                return render_template(
                    "login.html", form=form, next_url=next_url, error=_friendly_error(exc)
                ), 401 if isinstance(exc, AuthenticationError) else 400
            _set_identity(identity)
            flash(f"Welcome back, {identity.name}.", "success")
            return redirect(next_url or url_for("dashboard"))
        return render_template("login.html", form=form, next_url=next_url, error=None)

    @app.post("/logout")
    @login_required
    def logout() -> Any:
        flask_session.clear()
        flash("You have been logged out.", "info")
        return redirect(url_for("index"))

    @app.get("/dashboard")
    @login_required
    def dashboard() -> str:
        identity = _identity_session()
        assert identity is not None
        profile = services.profile.get_profile(identity)
        recommendations = services.graph.recommendations(identity, limit=6)
        return render_template("dashboard.html", profile=profile, recommendations=recommendations)

    @app.get("/profile")
    @login_required
    def my_profile() -> Any:
        identity = _identity_session()
        assert identity is not None
        return redirect(url_for("profile_view", username=identity.username))

    @app.get("/users/<username>")
    def profile_view(username: str) -> str:
        identity = _identity_session()
        if identity is not None and username.lower() == identity.username:
            profile = services.profile.get_profile(identity)
            is_self = True
            relationship = None
        else:
            profile = services.profile.get_public_profile(username)
            is_self = False
            relationship = None
            if identity is not None:
                relationship = services.graph.mutual_connections(identity, profile.username, limit=1).status
        return render_template(
            "profile.html",
            profile=profile,
            is_self=is_self,
            relationship=relationship,
        )

    @app.route("/profile/edit", methods=["GET", "POST"])
    @login_required
    def edit_profile() -> str | Any:
        identity = _identity_session()
        assert identity is not None
        profile = services.profile.get_profile(identity)
        if request.method == "POST":
            try:
                profile = services.profile.update_profile(
                    identity,
                    name=request.form.get("name", profile.name),
                    email=request.form.get("email") or None,
                    bio=request.form.get("bio") or None,
                    location=request.form.get("location") or None,
                )
            except CypherCircleError as exc:
                return render_template("edit_profile.html", profile=profile, error=_friendly_error(exc)), 400
            flask_session["identity"] = {"username": identity.username, "name": profile.name}
            flash("Your profile has been updated.", "success")
            return redirect(url_for("profile_view", username=profile.username))
        return render_template("edit_profile.html", profile=profile, error=None)

    @app.get("/connections")
    @login_required
    def connections() -> str:
        identity = _identity_session()
        assert identity is not None
        page = _page(request.args.get("page"))
        page_size = 15
        counts = services.graph.connection_counts(identity)
        following = services.graph.following(identity, skip=(page - 1) * page_size, limit=page_size)
        followers = services.graph.followers(identity, skip=(page - 1) * page_size, limit=page_size)
        return render_template(
            "connections.html",
            counts=counts,
            following=following,
            followers=followers,
            page=page,
            has_next=(page * page_size < max(counts.following, counts.followers)),
        )

    @app.route("/mutual", methods=["GET"])
    @login_required
    def mutual() -> str:
        identity = _identity_session()
        assert identity is not None
        first = request.args.get("first", "").strip()
        username = request.args.get("username", "").strip()
        page = _page(request.args.get("page"))
        result = None
        error = None
        if username:
            try:
                result = services.graph.mutual_connections(
                    identity,
                    username,
                    first=first or None,
                    skip=(page - 1) * 15,
                    limit=15,
                )
            except CypherCircleError as exc:
                error = _friendly_error(exc)
        return render_template(
            "mutual.html",
            first=first,
            username=username,
            result=result,
            error=error,
            page=page,
            has_next=bool(result and page * 15 < result.total),
        )

    @app.get("/recommendations")
    @login_required
    def recommendations() -> str:
        identity = _identity_session()
        assert identity is not None
        return render_template(
            "recommendations.html",
            recommendations=services.graph.recommendations(identity, limit=10),
        )

    @app.get("/users")
    @login_required
    def all_users() -> str:
        identity = _identity_session()
        assert identity is not None
        page = _page(request.args.get("page"))
        page_size = 25
        directory = services.search.all_users(
            identity, skip=(page - 1) * page_size, limit=page_size
        )
        start = (page - 1) * page_size + 1 if directory.users else 0
        return render_template(
            "users.html",
            directory=directory,
            page=page,
            pages=max(1, -(-directory.total // page_size)),
            start=start,
            end=start + len(directory.users) - 1 if directory.users else 0,
            has_next=page * page_size < directory.total,
        )

    @app.get("/separation")
    @login_required
    def separation() -> str:
        identity = _identity_session()
        assert identity is not None
        source = request.args.get("source", "").strip()
        target = request.args.get("target", "").strip()
        result = None
        error = None
        if source or target:
            try:
                result = services.graph.degrees_of_separation(identity, source, target)
            except CypherCircleError as exc:
                error = _friendly_error(exc)
        return render_template(
            "separation.html",
            source=source,
            target=target,
            result=result,
            error=error,
            max_hops=MAX_SEPARATION_HOPS,
        )

    @app.get("/search")
    @login_required
    def search_users() -> str:
        identity = _identity_session()
        assert identity is not None
        query = request.args.get("q", "")
        results = []
        error = None
        if query.strip():
            try:
                results = services.search.search_users(identity, query, limit=20)
            except CypherCircleError as exc:
                error = _friendly_error(exc)
        return render_template("search.html", query=query, results=results, error=error)

    @app.get("/popular")
    @login_required
    def popular() -> str:
        identity = _identity_session()
        assert identity is not None
        users = services.search.popular_users(exclude=[identity.username], limit=10)
        return render_template("popular.html", users=users)

    @app.route("/circles", methods=["GET"])
    @login_required
    def circles() -> str:
        identity = _identity_session()
        assert identity is not None
        popular_circles = services.profile.get_top_circles(limit=20)
        my_circles = services.profile.get_my_circles(identity)
        circles_by_id = {circle.circle_id: circle for circle in popular_circles}
        for circle in my_circles:
            circles_by_id.setdefault(circle.circle_id, circle)
        return render_template(
            "circles.html",
            circles=list(circles_by_id.values()),
            mine={circle.circle_id for circle in my_circles},
        )

    @app.post("/circles/join")
    @login_required
    def join_circle() -> Any:
        identity = _identity_session()
        assert identity is not None
        destination = _safe_next(request.form.get("next")) or url_for("circles")
        try:
            joined = services.profile.join_circle(identity, request.form.get("circle_id", ""))
            flash("You joined the circle." if joined else "You are already in that circle.", "success" if joined else "info")
        except CypherCircleError as exc:
            flash(_friendly_error(exc), "error")
        return redirect(destination)

    @app.post("/circles/leave")
    @login_required
    def leave_circle() -> Any:
        identity = _identity_session()
        assert identity is not None
        destination = _safe_next(request.form.get("next")) or url_for("circles")
        try:
            services.profile.leave_circle(identity, request.form.get("circle_id", ""))
            flash("You left the circle.", "success")
        except CypherCircleError as exc:
            flash(_friendly_error(exc), "error")
        return redirect(destination)

    @app.post("/users/<username>/follow")
    @login_required
    def follow_user(username: str) -> Any:
        identity = _identity_session()
        assert identity is not None
        destination = _safe_next(request.form.get("next")) or url_for("profile_view", username=username)
        try:
            services.graph.follow(identity, username)
            flash(f"You are now following @{username.lower()}.", "success")
        except CypherCircleError as exc:
            flash(_friendly_error(exc), "error")
        return redirect(destination)

    @app.post("/users/<username>/unfollow")
    @login_required
    def unfollow_user(username: str) -> Any:
        identity = _identity_session()
        assert identity is not None
        destination = _safe_next(request.form.get("next")) or url_for("profile_view", username=username)
        try:
            services.graph.unfollow(identity, username)
            flash(f"You unfollowed @{username.lower()}.", "success")
        except CypherCircleError as exc:
            flash(_friendly_error(exc), "error")
        return redirect(destination)

    @app.get("/visualization")
    @login_required
    def visualization() -> str:
        identity = _identity_session()
        assert identity is not None
        path: Path | None = None
        error = None
        warning = None
        try:
            path = services.visualization.generate(identity)
        except VisualizationError as exc:
            expected = Path(services.visualization.output_dir) / f"network_{identity.username}.html"
            if expected.is_file():
                path = expected
                warning = "The file was saved, but the browser could not be opened automatically."
            else:
                error = _friendly_error(exc)
        return render_template("visualization.html", available=path is not None, error=error, warning=warning)

    @app.get("/visualization/document")
    @login_required
    def visualization_document() -> Any:
        identity = _identity_session()
        assert identity is not None
        output_dir = Path(services.visualization.output_dir).resolve()
        path = (output_dir / f"network_{identity.username}.html").resolve()
        try:
            path.relative_to(output_dir)
        except ValueError:
            abort(404)
        if not path.is_file():
            abort(404)
        return send_file(path, mimetype="text/html", max_age=0)

    @app.errorhandler(CypherCircleError)
    def handle_service_error(error: CypherCircleError) -> tuple[str, int]:
        if isinstance(error, DatabaseUnavailableError):
            status = 503
        elif isinstance(error, NotFoundError):
            status = 404
        else:
            status = 400
        return render_template("error.html", message=_friendly_error(error), status=status), status

    @app.errorhandler(HTTPException)
    def handle_http_error(error: HTTPException) -> tuple[str, int]:
        message = error.description if error.code in {400, 404} else "The request could not be completed."
        return render_template("error.html", message=message, status=error.code or 500), error.code or 500

    @app.errorhandler(Exception)
    def handle_unexpected_error(error: Exception) -> tuple[str, int]:
        LOGGER.exception("Unexpected web error", exc_info=error)
        return render_template(
            "error.html",
            message="Something unexpected happened. Please try again.",
            status=500,
        ), 500

    return app


def main() -> int:
    try:
        settings = load_web_settings()
        app = create_app(web_settings=settings)
    except (ValueError, DatabaseUnavailableError) as exc:
        print(f"Cannot start Cypher Circle web UI: {_friendly_error(exc) if isinstance(exc, CypherCircleError) else exc}")
        return 1
    try:
        app.run(host=settings.host, port=settings.port, debug=settings.debug, use_reloader=False)
    except OSError:
        print(f"Cannot start Cypher Circle web UI: port {settings.port} is unavailable.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
