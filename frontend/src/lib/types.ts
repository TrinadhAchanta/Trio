export type Reasoning = "fast" | "balanced" | "deep";

export interface User {
  id: string;
  email: string;
  display_name: string;
  preferences: {
    default_model?: string;
    reasoning_mode?: Reasoning;
    theme?: "light" | "dark" | "system";
    allowed_providers?: string[];
    preferred_providers?: string[];
    allow_fallback?: boolean;
    memory_enabled?: boolean;
  };
  created_at: string;
}

export interface Conversation {
  id: string;
  title: string;
  folder: string | null;
  default_model: string | null;
  reasoning_mode: Reasoning;
  archived: boolean;
  agent_id?: string | null;
  created_at: string;
  updated_at: string;
}

export interface Message {
  id: string;
  conversation_id: string;
  parent_id: string | null;
  role: "user" | "assistant" | "system";
  content: string;
  provider: string | null;
  model: string | null;
  status: "streaming" | "complete" | "stopped" | "error";
  error: string | null;
  attachments: string[];
  sources?: Source[];
  input_tokens: number | null;
  output_tokens: number | null;
  latency_ms: number | null;
  rating: number | null;
  notice?: string | null;
  created_at: string;
}

export interface ConversationDetail extends Conversation {
  messages: Message[];
}

export interface ModelInfo {
  id: string;
  provider: string;
  model: string;
  label: string;
  tier: "fast" | "balanced" | "deep";
  vision: boolean;
  context_window: number | null;
  input_price_per_mtok: number | null;
  output_price_per_mtok: number | null;
  source: string;
  verified: boolean | null;
}

export interface ProviderInfo {
  name: string;
  label: string;
  configured: boolean;
  mock: boolean;
  health?: "healthy" | "degraded" | "down" | "recovering" | "unknown";
  models: ModelInfo[];
}

export interface ModelsResponse {
  providers: ProviderInfo[];
  auto_available: boolean;
}

export interface FileInfo {
  id: string;
  filename: string;
  content_type: string;
  kind: "text" | "image" | "pdf";
  size_bytes: number;
  created_at: string;
}

export interface Artifact {
  id: string;
  conversation_id: string | null;
  message_id: string | null;
  title: string;
  kind: "html" | "svg";
  current_version: number;
  created_at: string;
  updated_at: string;
}

export interface ArtifactDetail extends Artifact {
  versions: { version: number; content: string; source: string; created_at: string }[];
}

export interface Source {
  n: number;
  kb_id: string;
  document_id: string;
  chunk_id: string;
  title: string;
  content: string;
  score?: number;
}

export interface KBDocument {
  id: string;
  title: string;
  source: "upload" | "text";
  file_id: string | null;
  chars: number;
  chunk_count: number;
  created_at: string;
}

export interface KnowledgeBase {
  id: string;
  name: string;
  description: string | null;
  document_count: number;
  chunk_count: number;
  created_at: string;
  updated_at: string;
  documents?: KBDocument[];
}

export interface AgentInfo {
  id: string;
  name: string;
  description: string | null;
  instructions: string;
  model: string;
  reasoning_mode: Reasoning;
  knowledge_base_ids: string[];
  starters: string[];
  use_memory: boolean;
  created_at: string;
  updated_at: string;
}
