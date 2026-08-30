import { Panel } from "../components/Panel";
import { Button } from "../components/Button";
import { Icon } from "../components/Icon";
import { EmptyState } from "../components/EmptyState";

export function Behaviours() {
  return (
    <div className="body">
      <div className="pagehead">
        <h1>Behaviours</h1>
        <p>Everything Plumbline checks, written in plain English.</p>
      </div>
      <Panel style={{ marginTop: 18 }}>
        <EmptyState
          title="No filters match"
          description={
            <>
              342 behaviours exist for this repository, but none are tagged{" "}
              <span className="mono">payments</span> and owned by you.
            </>
          }
          actions={
            <>
              <Button>Clear filters</Button>
              <Button variant="pri">
                <Icon name="i-plus" size="xs" /> New behaviour
              </Button>
            </>
          }
        />
      </Panel>
    </div>
  );
}
