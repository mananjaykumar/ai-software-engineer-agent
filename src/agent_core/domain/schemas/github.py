from pydantic import BaseModel, ConfigDict


class GitHubUser(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    login: str


class GitHubRepositoryPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    name: str
    full_name: str
    owner: GitHubUser
    default_branch: str = "main"


class GitHubInstallation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int


class GitHubIssue(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    number: int
    title: str
    body: str | None = None
    state: str
    html_url: str


class GitHubIssueEvent(BaseModel):
    """Payload sent by GitHub when an issue is opened, edited, or labeled."""
    model_config = ConfigDict(extra="ignore")

    action: str
    issue: GitHubIssue
    repository: GitHubRepositoryPayload
    sender: GitHubUser
    installation: GitHubInstallation | None = None
