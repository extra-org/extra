export function conversationStorageKey(endpoint: string): string {
  return `agent-chat:${endpoint}`;
}

export function getStoredConversationId(endpoint: string, storage: Storage = localStorage): string | null {
  return storage.getItem(conversationStorageKey(endpoint));
}

export function setStoredConversationId(
  endpoint: string,
  conversationId: string,
  storage: Storage = localStorage,
): void {
  storage.setItem(conversationStorageKey(endpoint), conversationId);
}

export function removeStoredConversationId(endpoint: string, storage: Storage = localStorage): void {
  storage.removeItem(conversationStorageKey(endpoint));
}

export function getOrCreateUserId(endpoint: string, storage: Storage = localStorage): string {
  const key = `agent-chat:user:${endpoint}`;
  let id = storage.getItem(key);
  if (!id) {
    id = crypto.randomUUID();
    storage.setItem(key, id);
  }
  return id;
}
