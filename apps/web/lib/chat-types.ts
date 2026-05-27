export type ChatMessageKind =
  | 'user'
  | 'agent_thinking'
  | 'agent_action'
  | 'agent_message';

export interface ChatMessage {
  id: string;
  kind: ChatMessageKind;
  text: string;
  timestamp: number;
  isResolved?: boolean;
}
