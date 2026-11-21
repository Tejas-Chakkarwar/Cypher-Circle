class CypherCircleError(Exception):
    """Base error exposed by the application data-access boundary."""


class DatabaseUnavailableError(CypherCircleError):
    pass


class StartupDataError(CypherCircleError):
    pass


class VisualizationError(CypherCircleError):
    pass


class DataIntegrityError(CypherCircleError):
    def __init__(self, message: str, constraint_name: str | None = None) -> None:
        super().__init__(message)
        self.constraint_name = constraint_name


class QueryError(CypherCircleError):
    pass


class ValidationError(CypherCircleError):
    pass


class AuthenticationError(CypherCircleError):
    pass


class ConflictError(CypherCircleError):
    pass


class NotFoundError(CypherCircleError):
    pass


class SelfActionError(ValidationError):
    pass


class AlreadyFollowingError(ConflictError):
    pass


class NotFollowingError(ValidationError):
    pass


class MembershipError(ValidationError):
    pass


# Short name used by the service/API layer and by the design's login flow.
AuthError = AuthenticationError

