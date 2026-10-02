"""GitHub Projects v2 GraphQL client (read only), section 6."""

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import httpx

from app.config import FieldMappings

GRAPHQL_URL = "https://api.github.com/graphql"
MAX_WAIT_SECONDS = 900

_FIELD = "field { ... on ProjectV2FieldCommon { name } }"
QUERY = f"""
query($org: String!, $number: Int!, $after: String) {{
  organization(login: $org) {{
    projectV2(number: $number) {{
      id
      items(first: 100, after: $after) {{
        pageInfo {{ hasNextPage endCursor }}
        nodes {{
          id
          updatedAt
          fieldValues(first: 30) {{
            nodes {{
              ... on ProjectV2ItemFieldSingleSelectValue {{ name {_FIELD} }}
              ... on ProjectV2ItemFieldNumberValue {{ number {_FIELD} }}
              ... on ProjectV2ItemFieldIterationValue {{ title startDate duration {_FIELD} }}
            }}
          }}
          content {{
            __typename
            ... on Issue {{ id title url updatedAt assignees(first: 5) {{ nodes {{ login }} }}
                           labels(first: 30) {{ nodes {{ name }} }} }}
            ... on PullRequest {{ id title url updatedAt assignees(first: 5) {{ nodes {{ login }} }}
                                 labels(first: 30) {{ nodes {{ name }} }} }}
            ... on DraftIssue {{ title updatedAt assignees(first: 5) {{ nodes {{ login }} }} }}
          }}
        }}
      }}
    }}
  }}
}}
"""


class GitHubError(RuntimeError):
    pass


@dataclass(frozen=True)
class WorkItem:
    issue_node_id: str
    title: str
    url: str | None
    status: str | None
    assignee_login: str | None
    estimate: float | None
    sprint_name: str | None
    sprint_start: date | None
    sprint_days: int | None
    labels: tuple[str, ...]
    updated_at: datetime
    priority: str | None = None


@dataclass(frozen=True)
class ProjectData:
    project_node_id: str
    items: list[WorkItem]


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def parse_item(node: dict, fields: FieldMappings) -> WorkItem | None:
    """Turn one project item into a WorkItem. Returns None for items with no content."""
    content = node.get("content")
    if not content:
        return None
    status = estimate = sprint = start = days = priority = None
    for fv in node["fieldValues"]["nodes"]:
        name = (fv.get("field") or {}).get("name")
        if name == fields.status and "name" in fv:
            status = fv["name"]
        elif name == fields.priority and "name" in fv:
            priority = fv["name"]
        elif name == fields.estimate and "number" in fv:
            estimate = fv["number"]
        elif name == fields.sprint and "title" in fv:
            sprint = fv["title"]
            start = date.fromisoformat(fv["startDate"])
            days = fv["duration"]
    assignees = [a["login"] for a in content["assignees"]["nodes"]]
    labels = tuple(sorted(label["name"] for label in content.get("labels", {}).get("nodes", [])))
    return WorkItem(
        # Draft issues have no content id, so fall back to the project item id.
        issue_node_id=content.get("id") or node["id"],
        title=content["title"],
        url=content.get("url"),
        status=status,
        assignee_login=assignees[0] if assignees else None,
        estimate=estimate,
        sprint_name=sprint,
        sprint_start=start,
        sprint_days=days,
        labels=labels,
        updated_at=_dt(content.get("updatedAt") or node["updatedAt"]),
        priority=priority,
    )


class GitHubClient:
    def __init__(
        self,
        token: str,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        max_retries: int = 4,
    ):
        self._http = httpx.Client(
            transport=transport,
            timeout=30,
            headers={"Authorization": f"Bearer {token}"},
        )
        self._sleep = sleep
        self._max_retries = max_retries

    def _backoff_seconds(self, resp: httpx.Response, attempt: int) -> float:
        if "retry-after" in resp.headers:
            return float(resp.headers["retry-after"])
        if resp.headers.get("x-ratelimit-remaining") == "0" and "x-ratelimit-reset" in resp.headers:
            return max(0.0, float(resp.headers["x-ratelimit-reset"]) - time.time()) + 1
        return float(2**attempt)

    def _post(self, variables: dict) -> dict:
        for attempt in range(self._max_retries + 1):
            resp = self._http.post(GRAPHQL_URL, json={"query": QUERY, "variables": variables})
            rate_limited = resp.status_code == 429 or (
                resp.status_code == 403
                and ("retry-after" in resp.headers or resp.headers.get("x-ratelimit-remaining") == "0")
            )
            if rate_limited or resp.status_code >= 500:
                if attempt == self._max_retries:
                    raise GitHubError(f"GitHub still failing after retries: HTTP {resp.status_code}")
                self._sleep(min(self._backoff_seconds(resp, attempt), MAX_WAIT_SECONDS))
                continue
            if resp.status_code != 200:
                # Includes a plain 403: that is a permissions problem and retrying will not fix it.
                raise GitHubError(f"GitHub HTTP {resp.status_code}: {resp.text[:200]}")
            body = resp.json()
            errors = body.get("errors") or []
            if any(e.get("type") == "RATE_LIMITED" for e in errors) and attempt < self._max_retries:
                self._sleep(min(self._backoff_seconds(resp, attempt), MAX_WAIT_SECONDS))
                continue
            if errors:
                raise GitHubError(f"GitHub GraphQL errors: {errors[:2]}")
            return body["data"]
        raise GitHubError("unreachable")  # pragma: no cover

    def fetch_project(self, org: str, project_number: int, fields: FieldMappings) -> ProjectData:
        items: list[WorkItem] = []
        after: str | None = None
        while True:
            data = self._post({"org": org, "number": project_number, "after": after})
            project = data["organization"] and data["organization"]["projectV2"]
            if not project:
                raise GitHubError(f"project {org}#{project_number} not found or token lacks access to it")
            for node in project["items"]["nodes"]:
                item = parse_item(node, fields)
                if item:
                    items.append(item)
            page = project["items"]["pageInfo"]
            if not page["hasNextPage"]:
                return ProjectData(project["id"], items)
            after = page["endCursor"]


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    """Check X-Hub-Signature-256 (HMAC SHA256 of the raw body)."""
    import hashlib
    import hmac

    if not header or not header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


def sprint_end(start: date, days: int) -> date:
    return start + timedelta(days=days)
