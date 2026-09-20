'use client';

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from 'react';
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  Controls,
  MiniMap,
  Handle,
  Position,
  addEdge,
  applyNodeChanges,
  applyEdgeChanges,
  useReactFlow,
  type Node as FlowNode,
  type Edge,
  type Connection,
  type NodeProps,
} from '@xyflow/react';
import {
  Activity,
  ArrowDownToLine,
  ArrowUpFromLine,
  ArrowUpRight,
  Check,
  ChevronDown,
  ChevronRight,
  CircleHelp,
  Code2,
  Copy,
  GitBranch,
  GripVertical,
  KeyRound,
  Layers3,
  LoaderCircle,
  MessageSquare,
  MoreHorizontal,
  Play,
  Plus,
  Redo2,
  Save,
  Search,
  Send,
  Settings2,
  ShieldCheck,
  Sparkles,
  Square,
  Trash2,
  Undo2,
  Workflow as WorkflowIcon,
  X,
  Zap,
} from 'lucide-react';
import {
  api,
  seed,
  accent,
  friendlyStatus,
  sameExecutionGraph,
  saveCompletion,
  ApiError,
  importableWorkflow,
  connectionKind,
  paletteCategory,
  flowBinding,
  paletteCategories,
  type WorkflowEdge,
  type Workflow,
  type WorkflowNode,
  type Definition,
  type Run,
  type RunEvent,
  type Credential,
  type SavedWorkflow,
} from '@/lib/workflow';
import '@xyflow/react/dist/style.css';
import StudioDialog from './studio-dialog';
import ProviderSettings from './provider-settings';
import KnowledgeHub from './knowledge-hub';
import type { KnowledgeHubBase } from '@/lib/knowledge-hub';
import SourcePanel from './source-panel';
import PlatformSettings, {
  ConfigField,
  type ToolConnection,
} from './platform-settings';
import { sourcesForRun } from '@/lib/knowledge';
import {
  type AllowedModel,
  selectedModelId,
  latestRequestGuard,
  modelConfig,
  providerName,
} from '@/lib/models';
const displayValue = (value: unknown) =>
  ['string', 'number', 'boolean'].includes(typeof value) ? String(value) : '';

type CanvasNode = FlowNode<
  {
    spec: WorkflowNode;
    definition?: Definition;
    status?: string;
  },
  'workflow'
>;
const icons: Record<string, typeof Zap> = {
  chat_input: MessageSquare,
  manual_input: Zap,
  prompt: Code2,
  llm: Sparkles,
  agent: Sparkles,
  query: Search,
  retrieve: Search,
  tool_http: Code2,
  tool_email: Send,
  tool_python: Code2,
  condition: GitBranch,
  response: Send,
};

function WorkflowCard({ data, selected }: NodeProps<CanvasNode>) {
  const { spec, definition, status } = data;
  const Icon = Object.hasOwn(icons, spec.type) ? icons[spec.type] : Layers3;
  return (
    <div
      className={`workflow-card ${selected ? 'selected' : ''} ${status || ''}`}
    >
      <div className="node-heading">
        <span className={`node-icon ${accent[spec.type] || 'blue'}`}>
          <Icon size={19} />
        </span>
        <strong>{spec.label || definition?.name || spec.type}</strong>
        <MoreHorizontal size={17} className="muted" />
      </div>
      <p>
        {['llm', 'agent', 'query'].includes(spec.type)
          ? spec.config.provider === 'demo'
            ? 'Demo provider'
            : String(spec.config.model)
          : definition?.description || spec.type}
      </p>
      <div className="node-bottom">
        <span className="mono">{spec.type.replaceAll('_', ' ')}</span>
        <span className={`node-status ${status || ''}`}>
          {status === 'running' ? (
            <LoaderCircle size={12} className="spin" />
          ) : status === 'success' ? (
            <Check size={12} />
          ) : (
            <span className="tiny-dot" />
          )}
          {status || 'Ready'}
        </span>
      </div>
      {!['chat_input', 'manual_input'].includes(spec.type) && (
        <Handle type="target" position={Position.Top} />
      )}
      {spec.type === 'agent' && (
        <>
          <Handle type="target" id="tools" position={Position.Left} />
          <span className="port-label left">tools</span>
          <Handle type="source" id="agents" position={Position.Right} />
          <span className="port-label right">specialists</span>
        </>
      )}
      {spec.type === 'condition' ? (
        <>
          <Handle
            type="source"
            id="true"
            position={Position.Bottom}
            style={{ left: '30%' }}
          />
          <span className="port-label bottom" style={{ left: '25%' }}>
            true
          </span>
          <Handle
            type="source"
            id="false"
            position={Position.Bottom}
            style={{ left: '75%' }}
          />
          <span className="port-label bottom" style={{ left: '70%' }}>
            false
          </span>
        </>
      ) : (
        spec.type !== 'response' && (
          <Handle type="source" position={Position.Bottom} />
        )
      )}
    </div>
  );
}
const nodeTypes = { workflow: WorkflowCard };
const toCanvas = (workflow: Workflow): CanvasNode[] =>
  workflow.nodes.map((spec) => ({
    id: spec.id,
    type: 'workflow',
    position: spec.position,
    data: { spec },
  }));

function Editor() {
  const [nodes, setNodes] = useState<CanvasNode[]>(() => toCanvas(seed));
  const [edges, setEdges] = useState<
    (Edge & { kind?: WorkflowEdge['kind'] })[]
  >(seed.edges);
  const [name, setName] = useState(seed.name);
  const [description, setDescription] = useState(seed.description);
  const [workflowId, setWorkflowId] = useState<string>();
  const [workflowVersion, setWorkflowVersion] = useState<string>();
  const [saveConflict, setSaveConflict] = useState<{ id: string; workflow: Workflow; updated_at: string }>();
  const [dirty, setDirty] = useState(true);
  const [catalog, setCatalog] = useState<Definition[]>([]);
  const [saved, setSaved] = useState<SavedWorkflow[]>([]);
  const [credentials, setCredentials] = useState<Credential[]>([]);
  const [selected, setSelected] = useState<string>('agent');
  const [search, setSearch] = useState('');
  const [notice, setNotice] = useState('');
  const [errors, setErrors] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [running, setRunning] = useState(false);
  const [message, setMessage] = useState('How do AI workflows work?');
  const [run, setRun] = useState<Run>();
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [history, setHistory] = useState<Run[]>([]);
  const [tab, setTab] = useState('output');
  const [modal, setModal] = useState<'files' | 'help' | null>(null);
  const [connected, setConnected] = useState(false);
  const [leftPanel, setLeftPanel] = useState<
    'nodes' | 'providers' | 'knowledge' | 'connections'
  >('nodes');
  const [connections, setConnections] = useState<ToolConnection[]>([]);
  const [hubBases, setHubBases] = useState<KnowledgeHubBase[]>([]);
  const [allowedModels, setAllowedModels] = useState<AllowedModel[]>([]);
  const [undoStack, setUndo] = useState<Workflow[]>([]);
  const [redoStack, setRedo] = useState<Workflow[]>([]);
  const [inspectorOpen, setInspectorOpen] = useState(true);
  const source = useRef<EventSource | null>(null);
  const importRef = useRef<HTMLInputElement>(null);
  const canvas = useReactFlow<CanvasNode>();
  const current = useRef<Workflow>(seed);
  const documentToken = useRef(0);
  const workflow: Workflow = {
    version: 1,
    name,
    description,
    nodes: nodes.map((n) => ({ ...n.data.spec, position: n.position })),
    edges: edges.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      sourceHandle: e.sourceHandle,
      targetHandle: e.targetHandle,
      kind: e.kind || 'flow',
    })),
  };
  useLayoutEffect(() => {
    current.current = workflow;
  });
  const chosen = nodes.find((n) => n.id === selected)?.data.spec;
  const definition = catalog.find((d) => d.type === chosen?.type);
  const activeRun = useRef<string | undefined>(undefined);

  const initialModelChosen = useRef(false);
  const beginReload = useRef(latestRequestGuard());
  const reloadLists = useCallback(async () => {
    const isLatest = beginReload.current();
    const [defs, files, keys, runs, models, toolConnections, unifiedBases] =
      await Promise.all([
        api<Definition[]>('/nodes'),
        api<SavedWorkflow[]>('/workflows'),
        api<Credential[]>('/credentials'),
        api<Run[]>('/runs'),
        api<AllowedModel[]>('/models'),
        api<ToolConnection[]>('/connections'),
        api<KnowledgeHubBase[]>('/knowledge-bases').catch(() => []),
      ]);
    if (!isLatest()) return;
    setCatalog(defs);
    setSaved(files);
    setCredentials(keys);
    setAllowedModels(models);
    if (!initialModelChosen.current) {
      initialModelChosen.current = true;
      const first =
        models.find((m) => m.enabled && m.provider === 'ollama') ||
        models.find((m) => m.enabled);
      if (first && sameExecutionGraph(current.current, seed))
        setNodes(
          toCanvas({
            ...seed,
            nodes: seed.nodes.map((n) =>
              n.type === 'agent'
                ? { ...n, config: modelConfig(n.config, first) }
                : n,
            ),
          }),
        );
    }
    setHubBases(unifiedBases);
    setConnections(toolConnections);
    setHistory(runs);
    setConnected(true);
  }, []);
  useEffect(() => {
    void Promise.resolve()
      .then(reloadLists)
      .catch((e) => {
        setConnected(false);
        setNotice(e.message);
      });
    return () => source.current?.close();
  }, [reloadLists]);
  useEffect(() => {
    const fn = (e: BeforeUnloadEvent) => {
      if (dirty) {
        e.preventDefault();
      }
    };
    window.addEventListener('beforeunload', fn);
    return () => window.removeEventListener('beforeunload', fn);
  }, [dirty]);

  function snapshot() {
    setUndo((s) => [...s.slice(-39), structuredClone(current.current)]);
    setRedo([]);
    setDirty(true);
    setErrors([]);
  }
  function apply(w: Workflow) {
    setNodes(toCanvas(w));
    setEdges(w.edges);
    setName(w.name);
    setDescription(w.description);
    setSelected(
      w.nodes.find((n) => n.type === 'agent')?.id || w.nodes[0]?.id || '',
    );
    setErrors([]);
  }
  function undo() {
    if (!undoStack.length || running) return;
    setRedo((s) => [...s, structuredClone(current.current)]);
    apply(undoStack.at(-1)!);
    setUndo((s) => s.slice(0, -1));
    setDirty(true);
  }
  function redo() {
    if (!redoStack.length || running) return;
    setUndo((s) => [...s, structuredClone(current.current)]);
    apply(redoStack.at(-1)!);
    setRedo((s) => s.slice(0, -1));
    setDirty(true);
  }
  function modifyNode(change: Partial<WorkflowNode>) {
    if (!chosen) return;
    snapshot();
    setNodes((ns) =>
      ns.map((n) =>
        n.id === chosen.id
          ? { ...n, data: { ...n.data, spec: { ...n.data.spec, ...change } } }
          : n,
      ),
    );
  }
  function clearRun() {
    source.current?.close();
    source.current = null;
    activeRun.current = undefined;
    setRun(undefined);
    setEvents([]);
  }
  function loadDocument(w: Workflow, id?: string, updatedAt?: string) {
    documentToken.current += 1;
    clearRun();
    if (!id && sameExecutionGraph(w, seed)) {
      const first =
        allowedModels.find((m) => m.enabled && m.provider === 'ollama') ||
        allowedModels.find((m) => m.enabled);
      if (first)
        w = {
          ...w,
          nodes: w.nodes.map((n) =>
            n.type === 'agent'
              ? { ...n, config: modelConfig(n.config, first) }
              : n,
          ),
        };
    }
    apply(w);
    setWorkflowId(id);
    setWorkflowVersion(updatedAt);
    setSaveConflict(undefined);
    setDirty(!id);
    setUndo([]);
    setRedo([]);
    setModal(null);
    setTimeout(() => canvas.fitView({ padding: 0.22, duration: 250 }), 50);
  }
  function discardAllowed() {
    return (
      !dirty || window.confirm('Discard unsaved changes to this workflow?')
    );
  }
  async function load(id: string) {
    if (!discardAllowed()) return;
    try {
      const result = await api<{ workflow: Workflow; id: string; updated_at: string }>(
        `/workflows/${id}`,
      );
      loadDocument(result.workflow, id, result.updated_at);
    } catch (e) {
      setNotice((e as Error).message);
    }
  }
  async function save() {
    const startToken = documentToken.current;
    const savedSnapshot = structuredClone(current.current);
    setBusy(true);
    try {
      const result = await api<{ id: string; updated_at: string }>(
        workflowId ? `/workflows/${workflowId}` : '/workflows',
        workflowId ? 'PUT' : 'POST',
        workflowId ? { ...savedSnapshot, updated_at: workflowVersion } : savedSnapshot,
      );
      const completion = saveCompletion(
        startToken,
        documentToken.current,
        savedSnapshot,
        current.current,
      );
      if (completion.sameDocument) {
        setWorkflowId(result.id);
        setWorkflowVersion(result.updated_at);
        setSaveConflict(undefined);
        setDirty(completion.dirty);
        setNotice(
          completion.dirty
            ? 'Snapshot saved. Your newer edits are still unsaved.'
            : 'Workflow saved.',
        );
      }
      setSaved(await api<SavedWorkflow[]>('/workflows'));
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && startToken === documentToken.current) {
        const data = e.data as { current?: { id: string; workflow: Workflow; updated_at: string } };
        if (data.current) setSaveConflict(data.current);
      }
      setNotice((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function validate() {
    try {
      const result = await api<{ valid: boolean; errors: string[] }>(
        '/validate',
        'POST',
        current.current,
      );
      setErrors(result.errors);
      setNotice(
        result.valid
          ? 'Workflow is valid and ready to run.'
          : 'Fix the validation issues below.',
      );
      return result.valid;
    } catch (e) {
      setNotice((e as Error).message);
      return false;
    }
  }

  async function startRun() {
    setBusy(true);
    try {
      if (!(await validate())) return;
      clearRun();
      setTab('output');
      const body = { workflow: current.current, message };
      const result = await api<{ id: string }>('/runs', 'POST', body);
      activeRun.current = result.id;
      setRunning(true);
      setRun({
        id: result.id,
        status: 'running',
        output: '',
        error: '',
        events: [],
        created_at: new Date().toISOString(),
        ...body,
      });
      const stream = new EventSource(`/api/runs/${result.id}/events`);
      source.current = stream;
      stream.addEventListener('node', (event) => {
        if (source.current !== stream) return;
        const entry = JSON.parse((event as MessageEvent).data) as RunEvent;
        setEvents((old) =>
          old.some((e) => e.seq === entry.seq) ? old : [...old, entry],
        );
      });
      const finish = async () => {
        stream.close();
        try {
          const final = await api<Run>(`/runs/${result.id}`);
          if (source.current !== stream) return;
          setRun(final);
          setEvents(final.events);
          setHistory(await api<Run[]>('/runs'));
        } catch (e) {
          setNotice((e as Error).message);
        } finally {
          if (source.current === stream) {
            setRunning(false);
            activeRun.current = undefined;
          }
        }
      };
      stream.addEventListener('done', () => {
        void finish();
      });
      stream.onerror = () => {
        if (source.current !== stream) return;
        stream.close();
        setNotice('Live connection interrupted. Recovering the run status…');
        void recoverRun(result.id);
      };
    } catch (e) {
      setNotice((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function recoverRun(id: string) {
    try {
      const latest = await api<Run>(`/runs/${id}`);
      if (activeRun.current !== id) return;
      setRun(latest);
      setEvents(latest.events);
      if (['queued', 'running'].includes(latest.status)) {
        setTimeout(() => {
          if (activeRun.current === id) void recoverRun(id);
        }, 1000);
      } else {
        setRunning(false);
        activeRun.current = undefined;
        setHistory(await api<Run[]>('/runs'));
      }
    } catch {
      setNotice(
        'Cannot reach the backend. The run may still be active. Reconnect to inspect its status.',
      );
      setRunning(false);
      activeRun.current = undefined;
    }
  }
  async function resumeRun() {
    if (!run) return;
    setBusy(true);
    try {
      await api(`/runs/${run.id}/resume`, 'POST');
      setRun({ ...run, status: 'queued', error: '', output: '' });
      setRunning(true);
      activeRun.current = run.id;
      void recoverRun(run.id);
    } catch (e) {
      setNotice((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function cancel() {
    if (!run) return;
    try {
      await api(`/runs/${run.id}/cancel`, 'POST');
    } catch (e) {
      setNotice((e as Error).message);
    }
  }
  async function showRun(id: string) {
    clearRun();
    activeRun.current = id;
    try {
      const result = await api<Run>(`/runs/${id}`);
      if (activeRun.current !== id) return;
      setRun(result);
      setRunning(['running', 'queued'].includes(result.status));
      setEvents(result.events);
      setTab('output');
      if (['running', 'queued'].includes(result.status)) {
        activeRun.current = id;
        setRunning(true);
        void recoverRun(id);
      }
    } catch (e) {
      setNotice((e as Error).message);
    }
  }

  function addNode(type: string, position?: { x: number; y: number }) {
    const def = catalog.find((d) => d.type === type);
    if (!def) return;
    snapshot();
    const id = `${type}_${crypto.randomUUID().slice(0, 8)}`;
    const spec: WorkflowNode = {
      id,
      type,
      version: 1,
      label: def.name,
      position:
        position ||
        canvas.screenToFlowPosition({
          x: window.innerWidth / 2,
          y: window.innerHeight / 2 - 70,
        }),
      inputs: Object.fromEntries(Object.keys(def.inputs).map((k) => [k, ''])),
      config: Object.fromEntries(
        Object.entries(def.config_schema.properties)
          .filter(([, p]) => p.default !== undefined && p.default !== null)
          .map(([k, p]) => [k, p.default]),
      ),
    };
    if (['agent', 'query', 'llm'].includes(type)) {
      const model =
        allowedModels.find((m) => m.enabled && m.provider === 'ollama') ||
        allowedModels.find((m) => m.enabled);
      if (model) spec.config = modelConfig(spec.config, model);
    }
    setNodes((ns) => [
      ...ns,
      { id, type: 'workflow', position: spec.position, data: { spec } },
    ]);
    setSelected(id);
    setInspectorOpen(true);
  }
  function onConnect(connection: Connection) {
    const from = nodes.find((n) => n.id === connection.source)?.data.spec;
    const target = nodes.find((n) => n.id === connection.target)?.data.spec;
    if (!from || !target || from.id === target.id) return;
    const kind = connectionKind(
      from.type,
      target.type,
      connection.sourceHandle,
      connection.targetHandle,
    );
    if (!kind) {
      setNotice('These ports do not support this connection.');
      return;
    }
    snapshot();
    setEdges((es) =>
      addEdge({ ...connection, id: crypto.randomUUID(), kind }, es),
    );
    if (kind !== 'flow') {
      const callable =
        kind === 'tool' ? from.id : kind === 'agent' ? target.id : null;
      if (callable)
        setNodes((ns) =>
          ns.map((n) =>
            n.id === callable
              ? {
                  ...n,
                  data: { ...n.data, spec: { ...n.data.spec, inputs: {} } },
                }
              : n,
          ),
        );
      return;
    }
    const fromDef = catalog.find((d) => d.type === from?.type),
      toDef = catalog.find((d) => d.type === target?.type);
    if (from && target && fromDef && toDef) {
      const binding = flowBinding(from, target, fromDef, toDef);
      if (binding)
        setNodes((ns) =>
          ns.map((n) =>
            n.id === target.id
              ? {
                  ...n,
                  data: {
                    ...n.data,
                    spec: {
                      ...n.data.spec,
                      inputs: {
                        ...n.data.spec.inputs,
                        [binding.port]: `${from.id}.${binding.output}`,
                      },
                    },
                  },
                }
              : n,
          ),
        );
    }
  }
  function exportFile() {
    const blob = new Blob([JSON.stringify(current.current, null, 2)], {
      type: 'application/json',
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${name.replace(/[^a-z0-9]/gi, '-').toLowerCase() || 'workflow'}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }
  async function importFile(file?: File) {
    if (!file) return;
    try {
      if (file.size > 1000000)
        throw new Error('Workflow files must be smaller than 1 MB.');
      const value = JSON.parse(await file.text());
      const normalized = await api<{ workflow: Workflow; errors: string[] }>(
        '/validate',
        'POST',
        value,
      );
      const imported = importableWorkflow(
        normalized.workflow,
        catalog.map((d) => d.type),
      );
      if (discardAllowed()) {
        loadDocument(imported);
        setErrors(normalized.errors);
      }
    } catch (e) {
      setNotice(`Import failed: ${(e as Error).message}`);
    } finally {
      if (importRef.current) importRef.current.value = '';
    }
  }
  const statuses = Object.fromEntries(
    events.filter((e) => e.node_id).map((e) => [e.node_id, e.status]),
  );
  const renderedNodes = nodes.map((n) => ({
    ...n,
    data: {
      ...n.data,
      definition: catalog.find((d) => d.type === n.data.spec.type),
      status:
        run && sameExecutionGraph(run.workflow, workflow)
          ? statuses[n.id]
          : undefined,
    },
    selected: n.id === selected,
  }));
  const grouped: Record<string, Definition[]> = Object.fromEntries(
    paletteCategories
      .map((category) => [
        category,
        catalog.filter(
          (d) =>
            paletteCategory(d.type) === category &&
            `${d.name} ${d.description}`
              .toLowerCase()
              .includes(search.toLowerCase()),
        ),
      ])
      .filter(([, defs]) => (defs as Definition[]).length),
  );
  const successCount = events.filter(
    (e) => e.node_id && e.status === 'success',
  ).length;

  return (
    <main className="studio">
      <nav className="rail" aria-label="Workspace navigation">
        <button
          className="brand-mark"
          aria-label="Open workflows"
          disabled={running}
          onClick={() => setModal('files')}
        >
          <WorkflowIcon size={25} />
        </button>
        <button
          className="rail-button active"
          title="Workflow editor"
          aria-label="Workflow editor"
          onClick={() => {
            setModal(null);
            setLeftPanel('nodes');
          }}
        >
          <Layers3 size={21} />
        </button>
        <button
          className="rail-button"
          title="Run history"
          aria-label="Run history"
          onClick={() => {
            setLeftPanel('nodes');
            setTab('history');
          }}
        >
          <Activity size={21} />
        </button>
        <button
          className="rail-button"
          title="Providers & models"
          aria-label="Providers & models"
          onClick={() => setLeftPanel('providers')}
        >
          <KeyRound size={21} />
        </button>
        <div className="rail-bottom">
          <button
            className="rail-button"
            title="Help"
            aria-label="Help"
            onClick={() => setModal('help')}
          >
            <CircleHelp size={21} />
          </button>
          <span className="avatar">L</span>
        </div>
      </nav>
      <div className="app-shell">
        <header className="topbar">
          <div className="wordmark">
            relay<span className="version-tag">LOCAL</span>
          </div>
          <div className="workspace-label">
            Personal workspace <ChevronDown size={14} />
          </div>
          <span className={`connection ${connected ? 'online' : ''}`}>
            <span className="tiny-dot" />
            {connected ? 'Backend connected' : 'Backend offline'}
          </span>
          <button
            className="icon-button"
            title="Reconnect backend"
            aria-label="Reconnect backend"
            onClick={() => reloadLists().catch((e) => setNotice(e.message))}
          >
            <Activity size={17} />
          </button>
        </header>
        <div className="workflow-toolbar">
          <div className="workflow-heading">
            <div className="breadcrumb">
              <button disabled={running} onClick={() => setModal('files')}>
                Workflows
              </button>
              <ChevronRight size={12} />
              <span>Editor</span>
            </div>
            <div className="title-row">
              <input
                aria-label="Workflow name"
                value={name}
                disabled={running}
                maxLength={120}
                onChange={(e) => {
                  snapshot();
                  setName(e.target.value);
                }}
              />
              <span className="draft-label">{dirty ? 'Unsaved' : 'Saved'}</span>
            </div>
          </div>
          <div className="toolbar-actions">
            <button
              className="button secondary hide-small"
              disabled={running}
              onClick={() => void validate()}
            >
              <ShieldCheck size={16} /> Validate
            </button>
            <button
              className="button secondary"
              disabled={busy || running}
              onClick={() => void save()}
            >
              <Save size={16} />
              {busy ? 'Working…' : 'Save'}
            </button>
            <button
              className="button primary"
              disabled={busy || running || !connected}
              onClick={() => void startRun()}
            >
              <Play size={15} fill="currentColor" />
              Run workflow
            </button>
          </div>
        </div>
        <div
          className={`editor-grid ${leftPanel === 'knowledge' ? 'knowledge-workspace' : ''}`}
        >
          <aside
            className={`palette ${leftPanel !== 'nodes' ? 'provider-palette' : ''}`}
          >
            <div className="left-panel-tabs" aria-label="Workspace panels">
              <button
                aria-pressed={leftPanel === 'nodes'}
                className={leftPanel === 'nodes' ? 'active' : ''}
                onClick={() => setLeftPanel('nodes')}
              >
                Nodes
              </button>
              <button
                aria-pressed={leftPanel === 'providers'}
                className={leftPanel === 'providers' ? 'active' : ''}
                onClick={() => setLeftPanel('providers')}
              >
                Providers
              </button>
              <button
                aria-pressed={leftPanel === 'knowledge'}
                className={leftPanel === 'knowledge' ? 'active' : ''}
                onClick={() => setLeftPanel('knowledge')}
              >
                Knowledge
              </button>
              <button
                aria-pressed={leftPanel === 'connections'}
                className={leftPanel === 'connections' ? 'active' : ''}
                onClick={() => setLeftPanel('connections')}
              >
                Connections
              </button>
            </div>
            {leftPanel === 'connections' ? (
              <PlatformSettings
                connections={connections}
                refresh={reloadLists}
                disabled={running}
              />
            ) : leftPanel === 'knowledge' ? (
              <KnowledgeHub
                connections={connections}
                refresh={reloadLists}
                disabled={running}
              />
            ) : leftPanel === 'providers' ? (
              <ProviderSettings
                models={allowedModels}
                credentials={credentials}
                onChange={reloadLists}
              />
            ) : (
              <>
                <div className="panel-title">
                  <span>Node library</span>
                  <span className="count">
                    {catalog.filter((d) => paletteCategory(d.type)).length}
                  </span>
                </div>
                <label className="search-box">
                  <Search size={15} />
                  <input
                    placeholder="Search nodes…"
                    aria-label="Search nodes"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                  />
                </label>
                <div className="palette-list">
                  {Object.entries(grouped).map(([category, defs]) => (
                    <section key={category}>
                      <h3>{category}</h3>
                      {defs.map((d) => {
                        const Icon = Object.hasOwn(icons, d.type)
                          ? icons[d.type]
                          : Layers3;
                        return (
                          <button
                            className="palette-node"
                            key={d.type}
                            draggable={!running}
                            disabled={running}
                            onDragStart={(e) =>
                              e.dataTransfer.setData(
                                'application/relay-node',
                                d.type,
                              )
                            }
                            onClick={() => addNode(d.type)}
                            title={d.description}
                          >
                            <span className={`library-icon ${accent[d.type]}`}>
                              <Icon size={17} />
                            </span>
                            <span>{d.name}</span>
                            <GripVertical size={14} className="grip" />
                          </button>
                        );
                      })}
                    </section>
                  ))}
                  {!catalog.length && (
                    <p className="helper">
                      Connect the backend to load available nodes.
                    </p>
                  )}
                  {catalog.length > 0 && !Object.keys(grouped).length && (
                    <p className="helper">No matching nodes.</p>
                  )}
                </div>
                <div className="palette-footer">
                  <span className="mini-kicker">BUILD YOUR FLOW</span>
                  <p>Drag a node onto the canvas, then connect its handles.</p>
                  <button
                    className="text-button"
                    onClick={() => setModal('help')}
                  >
                    Quick guide <ArrowUpRight size={14} />
                  </button>
                </div>
              </>
            )}
          </aside>
          <section className="canvas-column">
            <div className="canvas-top">
              <div className="canvas-tab">
                <WorkflowIcon size={15} /> Workflow{' '}
                <span className="tiny-dot" />
              </div>
              <div className="canvas-tools">
                <button
                  className="icon-button"
                  title="Undo"
                  aria-label="Undo"
                  disabled={!undoStack.length || running}
                  onClick={undo}
                >
                  <Undo2 size={16} />
                </button>
                <button
                  className="icon-button"
                  title="Redo"
                  aria-label="Redo"
                  disabled={!redoStack.length || running}
                  onClick={redo}
                >
                  <Redo2 size={16} />
                </button>
                <span className="tool-divider" />
                <button
                  className="icon-button"
                  aria-label="Import workflow JSON"
                  title="Import JSON"
                  disabled={running}
                  onClick={() => importRef.current?.click()}
                >
                  <ArrowUpFromLine size={16} />
                </button>
                <button
                  className="icon-button"
                  aria-label="Export workflow JSON"
                  title="Export JSON"
                  onClick={exportFile}
                >
                  <ArrowDownToLine size={16} />
                </button>
                <button
                  className="icon-button"
                  aria-label="Toggle node settings"
                  title="Node settings"
                  onClick={() => setInspectorOpen((v) => !v)}
                >
                  <Settings2 size={16} />
                </button>
              </div>
            </div>
            <div
              className="canvas"
              onDragOver={(e) => {
                e.preventDefault();
                e.dataTransfer.dropEffect = 'move';
              }}
              onDrop={(e) => {
                e.preventDefault();
                if (!running)
                  addNode(
                    e.dataTransfer.getData('application/relay-node'),
                    canvas.screenToFlowPosition({ x: e.clientX, y: e.clientY }),
                  );
              }}
            >
              <ReactFlow<CanvasNode, Edge>
                nodes={renderedNodes}
                edges={edges.map((e) => ({
                  ...e,
                  type: 'smoothstep',
                  label: e.kind && e.kind !== 'flow' ? e.kind : undefined,
                  style: {
                    stroke:
                      e.kind === 'tool'
                        ? '#b98b3b'
                        : e.kind === 'agent'
                          ? '#9772bb'
                          : '#aeb9c7',
                    strokeWidth: 1.7,
                    strokeDasharray:
                      e.kind && e.kind !== 'flow' ? '5 3' : undefined,
                  },
                  animated: running,
                }))}
                nodeTypes={nodeTypes}
                onNodesChange={(changes) => {
                  if (running) return;
                  const relevant = changes.filter((c) => c.type !== 'select');
                  if (relevant.some((c) => c.type === 'remove')) snapshot();
                  if (relevant.some((c) => c.type === 'position'))
                    setDirty(true);
                  setNodes((ns) => applyNodeChanges(relevant, ns));
                }}
                onEdgesChange={(changes) => {
                  if (running) return;
                  if (changes.some((c) => c.type === 'remove')) snapshot();
                  setEdges((es) => applyEdgeChanges(changes, es));
                }}
                onNodeDragStart={() => snapshot()}
                onNodeClick={(_, n) => {
                  setSelected(n.id);
                  setInspectorOpen(true);
                }}
                onPaneClick={() => setSelected('')}
                onConnect={onConnect}
                nodesDraggable={!running}
                nodesConnectable={!running}
                edgesReconnectable={false}
                deleteKeyCode={running ? null : ['Backspace', 'Delete']}
                fitView
                fitViewOptions={{ padding: 0.22, maxZoom: 1 }}
                minZoom={0.3}
                maxZoom={1.5}
                proOptions={{ hideAttribution: true }}
              >
                <Background color="#d5dbe3" gap={20} size={1} />
                <Controls showInteractive={false} />
                <MiniMap
                  nodeColor={(n) => (n.id === selected ? '#6aa89b' : '#d8e1e8')}
                  maskColor="rgba(247,249,251,.6)"
                  pannable
                  zoomable
                />
              </ReactFlow>
              <div className="canvas-caption">
                <span className="tiny-dot" /> {nodes.length} nodes{' '}
                <span>·</span> {edges.length} connections
              </div>
            </div>
            {errors.length > 0 && (
              <div className="validation-errors" role="alert">
                <strong>Workflow needs attention</strong>
                <button
                  aria-label="Dismiss validation errors"
                  onClick={() => setErrors([])}
                >
                  <X size={15} />
                </button>
                <ul>
                  {errors.map((error, i) => (
                    <li key={i}>{error}</li>
                  ))}
                </ul>
              </div>
            )}
            <section className="run-panel">
              <div className="run-tabs">
                <div>
                  {[
                    ['output', 'Run output'],
                    ['events', 'Execution log'],
                    ['history', 'History'],
                  ].map(([id, label]) => (
                    <button
                      key={id}
                      className={tab === id ? 'active' : ''}
                      onClick={() => setTab(id)}
                    >
                      {id === 'events' ? (
                        <Activity size={15} />
                      ) : id === 'history' ? (
                        <Layers3 size={15} />
                      ) : (
                        <Play size={14} />
                      )}{' '}
                      {label}
                      {id === 'events' && events.length > 0 && (
                        <span className="count">{events.length}</span>
                      )}
                    </button>
                  ))}
                </div>
                <span className={`run-badge ${run?.status || ''}`}>
                  {running ? (
                    <LoaderCircle size={13} className="spin" />
                  ) : (
                    <span className="tiny-dot" />
                  )}
                  {run ? friendlyStatus[run.status] : 'Not run yet'}
                </span>
              </div>
              {tab === 'output' && (
                <div className="run-content">
                  <div className="test-input">
                    <label htmlFor="test-message">Test input</label>
                    <textarea
                      id="test-message"
                      value={message}
                      onChange={(e) => setMessage(e.target.value)}
                      disabled={running}
                      maxLength={20000}
                      placeholder="Enter a message to test your workflow"
                    />
                    <div className="input-caption">
                      <span>Passed to your input node</span>
                      {running ? (
                        <button
                          className="text-button danger"
                          onClick={() => void cancel()}
                        >
                          <Square size={12} /> Cancel run
                        </button>
                      ) : (
                        <button
                          className="text-button"
                          disabled={busy || !connected}
                          onClick={() => void startRun()}
                        >
                          Run again <Play size={12} />
                        </button>
                      )}
                    </div>
                  </div>
                  <div className="response-output">
                    <div className="output-label">
                      <span>Response</span>
                      {run && ['failed', 'cancelled'].includes(run.status) && (
                        <button
                          className="text-button"
                          disabled={running || busy}
                          onClick={() => void resumeRun()}
                        >
                          Resume saved run <Play size={12} />
                        </button>
                      )}
                      {run?.output && (
                        <button
                          className="icon-button"
                          aria-label="Copy response"
                          onClick={() =>
                            navigator.clipboard
                              .writeText(run.output)
                              .then(() => setNotice('Response copied.'))
                              .catch(() =>
                                setNotice(
                                  'Clipboard unavailable. Select the response text to copy it.',
                                ),
                              )
                          }
                        >
                          <Copy size={14} />
                        </button>
                      )}
                    </div>
                    {/* oxlint-disable jsx-a11y/no-noninteractive-tabindex -- Scroll regions need keyboard focus for arrow and page navigation. */}
                    <section
                      className="response-scroll"
                      aria-label="Response and sources"
                      tabIndex={0}
                    >
                      {run?.output ? (
                        <pre>{run.output}</pre>
                      ) : run?.error ? (
                        <p className="error-text">{run.error}</p>
                      ) : (
                        <div className="empty-output">
                          {running ? (
                            <LoaderCircle size={22} className="spin" />
                          ) : (
                            <Send size={23} />
                          )}
                          <strong>
                            {running
                              ? 'Running your workflow…'
                              : run?.status === 'cancelled'
                                ? 'Run cancelled'
                                : 'Your response will appear here'}
                          </strong>
                          <span>
                            {running
                              ? `${successCount} nodes completed`
                              : run?.status === 'cancelled'
                                ? 'Edit the input and run again when ready.'
                                : 'Send a test message to see the complete flow in action.'}
                          </span>
                        </div>
                      )}
                      <SourcePanel sources={sourcesForRun(run, events)} />
                    </section>
                    {/* oxlint-enable jsx-a11y/no-noninteractive-tabindex */}
                  </div>
                </div>
              )}
              {tab === 'events' && (
                <div className="event-list">
                  {events.length ? (
                    events.map((e) => (
                      <details key={e.seq} className="event-row">
                        <summary>
                          <span className={`event-dot ${e.status}`} />
                          <span>{e.node_id || 'Workflow'}</span>
                          <span className="event-status">
                            {e.status}
                            {e.cached ? ' · restored' : ''}
                          </span>
                          <span className="mono muted">
                            {e.duration_ms !== undefined
                              ? `${e.duration_ms} ms`
                              : ''}
                            {e.usage
                              ? ` · ${e.usage.prompt_tokens}+${e.usage.completion_tokens} tok`
                              : ''}
                          </span>
                          <ChevronDown size={13} />
                        </summary>
                        <pre>
                          {JSON.stringify(
                            e.inputs ||
                              e.outputs || { status: e.status, error: e.error },
                            null,
                            2,
                          )}
                        </pre>
                      </details>
                    ))
                  ) : (
                    <p className="helper">
                      Run a workflow to inspect node inputs, outputs, timing,
                      and errors.
                    </p>
                  )}
                </div>
              )}
              {tab === 'history' && (
                <div className="history-list">
                  {history.length ? (
                    history.map((h) => (
                      <button
                        key={h.id}
                        disabled={running}
                        onClick={() => void showRun(h.id)}
                      >
                        <span className={`event-dot ${h.status}`} />
                        <strong>{h.name}</strong>
                        <span>{friendlyStatus[h.status]}</span>
                        <time>{new Date(h.created_at).toLocaleString()}</time>
                        <ChevronRight size={15} />
                      </button>
                    ))
                  ) : (
                    <p className="helper">
                      No runs yet. Your executions will be saved here.
                    </p>
                  )}
                </div>
              )}
            </section>
          </section>
          {inspectorOpen && (
            <aside className="inspector">
              <div className="panel-title">
                <span>Node settings</span>
                <button
                  className="icon-button"
                  aria-label="Close node settings"
                  onClick={() => setInspectorOpen(false)}
                >
                  <X size={16} />
                </button>
              </div>
              {chosen ? (
                <>
                  <div className="inspector-intro">
                    <span className={`node-icon ${accent[chosen.type]}`}>
                      {(() => {
                        const Icon = Object.hasOwn(icons, chosen.type)
                          ? icons[chosen.type]
                          : Layers3;
                        return <Icon size={22} />;
                      })()}
                    </span>
                    <div>
                      <h2>{definition?.name || chosen.type}</h2>
                      <span className="mono muted">
                        v1 · {definition?.category || 'Node'}
                      </span>
                    </div>
                  </div>
                  <p className="inspector-description">
                    {definition?.description}
                  </p>
                  <div className="inspector-form">
                    <label>
                      Node name
                      <input
                        disabled={running}
                        value={chosen.label}
                        onChange={(e) => modifyNode({ label: e.target.value })}
                      />
                    </label>
                    {definition &&
                      Object.keys(definition.inputs).length > 0 &&
                      !edges.some(
                        (e) =>
                          (e.kind === 'tool' && e.source === chosen.id) ||
                          (e.kind === 'agent' && e.target === chosen.id),
                      ) && (
                        <section className="settings-section">
                          <h3>INPUT BINDINGS</h3>
                          {Object.keys(definition.inputs).map((port) => (
                            <label key={port}>
                              {port}
                              <select
                                disabled={running}
                                value={chosen.inputs[port] || ''}
                                onChange={(e) =>
                                  modifyNode({
                                    inputs: {
                                      ...chosen.inputs,
                                      [port]: e.target.value,
                                    },
                                  })
                                }
                              >
                                <option value="">Choose upstream output</option>
                                {nodes
                                  .filter((n) =>
                                    edges.some(
                                      (e) =>
                                        (!e.kind || e.kind === 'flow') &&
                                        e.source === n.id &&
                                        e.target === chosen.id,
                                    ),
                                  )
                                  .flatMap((n) =>
                                    Object.keys(
                                      catalog.find(
                                        (d) => d.type === n.data.spec.type,
                                      )?.outputs || {},
                                    ).map((output) => (
                                      <option
                                        key={`${n.id}.${output}`}
                                        value={`${n.id}.${output}`}
                                      >
                                        {n.data.spec.label || n.id} → {output}
                                      </option>
                                    )),
                                  )}
                              </select>
                            </label>
                          ))}
                        </section>
                      )}
                    {definition &&
                      Object.keys(definition.config_schema.properties).length >
                        0 && (
                        <section className="settings-section">
                          <h3>CONFIGURATION</h3>
                          {['llm', 'agent', 'query'].includes(chosen.type) && (
                            <label>
                              Model
                              <select
                                disabled={running}
                                value={
                                  chosen.config.provider === 'demo'
                                    ? 'demo'
                                    : selectedModelId(
                                        allowedModels,
                                        chosen.config,
                                      )
                                }
                                onChange={(e) => {
                                  if (e.target.value === 'demo')
                                    modifyNode({
                                      config: {
                                        ...chosen.config,
                                        provider: 'demo',
                                        credential_id: '',
                                      },
                                    });
                                  else {
                                    const model = allowedModels.find(
                                      (m) =>
                                        m.id === e.target.value && m.enabled,
                                    );
                                    if (model)
                                      modifyNode({
                                        config: modelConfig(
                                          chosen.config,
                                          model,
                                        ),
                                      });
                                  }
                                }}
                              >
                                <option value="" disabled>
                                  Choose an enabled model
                                </option>
                                {chosen.type === 'llm' && (
                                  <option value="demo">
                                    Demo · no model called
                                  </option>
                                )}
                                {allowedModels
                                  .filter((m) => m.enabled)
                                  .map((m) => (
                                    <option key={m.id} value={m.id}>
                                      {providerName(m.provider)} · {m.model}
                                      {m.credential_id
                                        ? ` · ${credentials.find((c) => c.id === m.credential_id)?.name || 'API key'}`
                                        : ''}
                                    </option>
                                  ))}
                              </select>
                              {chosen.config.provider !== 'demo' &&
                                !selectedModelId(
                                  allowedModels,
                                  chosen.config,
                                ) && (
                                  <span className="error-text">
                                    The current model is not enabled. Configure
                                    a model on the left, then select it here.
                                  </span>
                                )}
                              <button
                                className="text-button"
                                onClick={() => setLeftPanel('providers')}
                              >
                                Manage providers & allowed models
                              </button>
                            </label>
                          )}
                          {Object.entries(definition.config_schema.properties)
                            .filter(
                              ([key]) =>
                                // storage_path is a retired field kept only so saved JSON loads.
                                !(
                                  ['retrieve', 'query'].includes(chosen.type) &&
                                  key === 'storage_path'
                                ) &&
                                !(
                                  ['llm', 'agent', 'query'].includes(
                                    chosen.type,
                                  ) &&
                                  [
                                    'provider',
                                    'model',
                                    'credential_id',
                                  ].includes(key)
                                ),
                            )
                            .map(([key, property]) => {
                              const update = (value: unknown) => {
                                const config = { ...chosen.config };
                                if (value === undefined) delete config[key];
                                else config[key] = value;
                                modifyNode({ config });
                              };
                              const list =
                                key === 'connection_id'
                                  ? connections.filter(
                                      (c) =>
                                        c.provider ===
                                        chosen.type.replace('tool_', ''),
                                    )
                                  : key === 'knowledge_base_id'
                                    ? hubBases
                                    : null;
                              return list ? (
                                <label key={key}>
                                  {property.title || key}
                                  <select
                                    disabled={running}
                                    value={displayValue(
                                      chosen.config[key] || '',
                                    )}
                                    onChange={(e) => update(e.target.value)}
                                  >
                                    <option value="">Choose a resource</option>
                                    {list.map((item) => (
                                      <option key={item.id} value={item.id}>
                                        {item.name}
                                      </option>
                                    ))}
                                  </select>
                                  <button
                                    className="text-button"
                                    onClick={() =>
                                      setLeftPanel(
                                        key === 'knowledge_base_id'
                                          ? 'knowledge'
                                          : 'connections',
                                      )
                                    }
                                  >
                                    Manage shared resources
                                  </button>
                                </label>
                              ) : (
                                <ConfigField
                                  key={`${chosen.id}:${key}`}
                                  name={key}
                                  property={
                                    key === 'enable_writes'
                                      ? {
                                          ...property,
                                          title:
                                            'Allow sending and changing external data',
                                          description:
                                            'Enable deliberately for this node’s configured write operation.',
                                        }
                                      : key === 'temperature' &&
                                          chosen.config.provider === 'claude'
                                        ? {
                                            ...property,
                                            anyOf: property.anyOf?.map((p) =>
                                              p.type === 'number'
                                                ? { ...p, maximum: 1 }
                                                : p,
                                            ),
                                            description:
                                              'Claude accepts temperature or top-p. Leave the other blank.',
                                          }
                                        : property
                                  }
                                  value={chosen.config[key]}
                                  disabled={running}
                                  onChange={update}
                                />
                              );
                            })}
                          {chosen.type === 'agent' && (
                            <p className="helper">
                              Connect tools to the left port. Connect the right
                              port to a specialist agent’s top input. The bottom
                              port continues this workflow.
                            </p>
                          )}
                        </section>
                      )}
                    {['llm', 'agent', 'query'].includes(chosen.type) &&
                      chosen.config.provider === 'demo' && (
                        <div className="info-note">
                          <Sparkles size={16} />
                          <p>
                            <strong>Test without a key</strong>Demo mode echoes
                            the prompt. Enable an Ollama, OpenAI or Claude model
                            on the left to generate a model response.
                          </p>
                        </div>
                      )}
                    {chosen.type === 'prompt' && (
                      <p className="helper">
                        Insert <code>{'{message}'}</code> to use the bound
                        input. Double braces escape literal JSON braces.
                      </p>
                    )}
                    <section className="settings-section">
                      <h3>OUTPUTS</h3>
                      {Object.entries(definition?.outputs || {}).map(
                        ([port, type]) => (
                          <div className="output-port" key={port}>
                            <span className="tiny-dot" />
                            <code>{port}</code>
                            <span>{type}</span>
                          </div>
                        ),
                      )}
                    </section>
                    <button
                      className="button delete-node"
                      disabled={running}
                      onClick={() => {
                        snapshot();
                        setNodes((ns) => ns.filter((n) => n.id !== selected));
                        setEdges((es) =>
                          es.filter(
                            (e) =>
                              e.source !== selected && e.target !== selected,
                          ),
                        );
                        setSelected('');
                      }}
                    >
                      <Trash2 size={14} /> Delete node
                    </button>
                  </div>
                </>
              ) : (
                <div className="inspector-empty">
                  <Settings2 size={25} />
                  <h2>Select a node</h2>
                  <p>Configure its inputs, settings, and outputs here.</p>
                </div>
              )}
            </aside>
          )}
        </div>
        <footer className="statusbar">
          <span>
            <span className="tiny-dot" />
            Durable local workspace
          </span>
          <span>
            LangGraph runtime <span className="separator">/</span> Workflow
            schema v1
          </span>
        </footer>
      </div>
      {saveConflict && (
        <StudioDialog title="Workflow changed elsewhere" onClose={() => setSaveConflict(undefined)}>
          <h2>Workflow changed elsewhere</h2>
          <p>This workflow changed elsewhere. Your local edits have been preserved. Export them before reloading if you want to keep both versions.</p>
          <button className="button" onClick={exportFile}>Export local edits</button>
          <button className="button primary" onClick={() => {
            if (discardAllowed()) loadDocument(saveConflict.workflow, saveConflict.id, saveConflict.updated_at);
          }}>Reload server version</button>
          <button className="button" onClick={() => setSaveConflict(undefined)}>Keep editing</button>
        </StudioDialog>
      )}
      {notice && (
        <output className="toast">
          <span>{notice}</span>
          <button
            aria-label="Dismiss notification"
            onClick={() => setNotice('')}
          >
            <X size={16} />
          </button>
        </output>
      )}
      <input
        type="file"
        accept="application/json,.json"
        ref={importRef}
        hidden
        onChange={(e) => void importFile(e.target.files?.[0])}
      />
      {modal && (
        <StudioDialog
          title={modal === 'files' ? 'Saved workflows' : 'Quick guide'}
          onClose={() => setModal(null)}
        >
          <div className="modal-heading">
            <h2>
              {modal === 'files'
                ? 'Your workflows'
                : 'Build your first workflow'}
            </h2>
            <button
              className="icon-button"
              aria-label="Close dialog"
              onClick={() => setModal(null)}
            >
              <X size={19} />
            </button>
          </div>
          {modal === 'files' && (
            <>
              <button
                className="button primary"
                disabled={running}
                onClick={() => {
                  if (discardAllowed()) loadDocument(structuredClone(seed));
                }}
              >
                <Plus size={16} /> New workflow
              </button>
              <div className="saved-list">
                {saved.length ? (
                  saved.map((file) => (
                    <button key={file.id} onClick={() => void load(file.id)}>
                      <WorkflowIcon size={18} />
                      <span>
                        <strong>{file.name}</strong>
                        <small>
                          {new Date(file.updated_at).toLocaleString()}
                        </small>
                      </span>
                      <ChevronRight size={16} />
                    </button>
                  ))
                ) : (
                  <p className="helper">
                    No saved workflows. Save the current canvas to keep it here.
                  </p>
                )}
              </div>
            </>
          )}
          {modal === 'help' && (
            <>
              <ol className="guide">
                <li>
                  <strong>Compose your workflow</strong>
                  <p>
                    Click or drag nodes from the library. Connect an output
                    handle to the next node’s input handle.
                  </p>
                </li>
                <li>
                  <strong>Map inputs and configure nodes</strong>
                  <p>
                    Select a node to bind upstream outputs. Conditions need both
                    a true and false connection, each leading to a response.
                  </p>
                </li>
                <li>
                  <strong>Test, inspect, iterate</strong>
                  <p>
                    Enter a message and run. The execution log shows real node
                    inputs, outputs, timings and errors. Demo mode needs no
                    credential.
                  </p>
                </li>
                <li>
                  <strong>Save or export</strong>
                  <p>
                    Save to the local backend or export a portable JSON file.
                    Changes are not autosaved.
                  </p>
                </li>
              </ol>
              <div className="info-note">
                <ShieldCheck size={16} />
                <p>
                  This foundation supports single-user, acyclic workflows.
                  Loops, parallel execution, approvals and team permissions are
                  future releases.
                </p>
              </div>
            </>
          )}
        </StudioDialog>
      )}
    </main>
  );
}
export default function WorkflowEditor() {
  return (
    <ReactFlowProvider>
      <Editor />
    </ReactFlowProvider>
  );
}
