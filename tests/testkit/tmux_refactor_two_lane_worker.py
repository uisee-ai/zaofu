"""Two-lane scripted semantics activated by real stdin and signed result CLI."""

from tests.testkit.refactor_two_lane_route import TwoLaneStrongRefactorRoute
from tests.testkit.tmux_general_worker import observe_worker
from tests.testkit.tmux_issue_worker import IssueWorker
from tests.testkit.tmux_refactor_worker import RefactorWorker
from tests.testkit.tmux_refactor_strong_worker import StrongRefactorWorker


class TwoLaneRefactorWorker(TwoLaneStrongRefactorRoute):
    __init__ = IssueWorker.__init__
    complete = RefactorWorker.complete
    submit_semantic = StrongRefactorWorker.submit_semantic


def main() -> None:
    observe_worker(TwoLaneRefactorWorker)


if __name__ == "__main__":
    main()
