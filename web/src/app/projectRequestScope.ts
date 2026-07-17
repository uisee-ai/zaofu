export interface ProjectRequestTicket {
  generation: number;
  projectId: string;
}

/**
 * Invalidates asynchronous project reads when the operator changes project.
 * Network requests may still finish, but stale results cannot mutate the
 * active project's UI state.
 */
export class ProjectRequestScope {
  private generation = 0;
  private projectId: string;

  constructor(projectId = "") {
    this.projectId = projectId;
  }

  activate(projectId: string): void {
    if (projectId === this.projectId) return;
    this.projectId = projectId;
    this.generation += 1;
  }

  capture(projectId = this.projectId): ProjectRequestTicket {
    return { generation: this.generation, projectId };
  }

  isCurrent(ticket: ProjectRequestTicket): boolean {
    return ticket.generation === this.generation
      && ticket.projectId === this.projectId;
  }
}
