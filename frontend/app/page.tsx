import AuthGate from '@/components/auth-gate';
import WorkflowEditor from '@/components/workflow-editor';
export default function Home() {
  return (
    <AuthGate>
      <WorkflowEditor />
    </AuthGate>
  );
}
