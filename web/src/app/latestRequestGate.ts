export class LatestRequestGate {
  private sequence = 0;

  issue(): number {
    this.sequence += 1;
    return this.sequence;
  }

  invalidate(): void {
    this.sequence += 1;
  }

  isCurrent(ticket: number): boolean {
    return ticket === this.sequence;
  }
}
