export type ProductModule = {
  id: 'conversation' | 'writing' | 'tasks' | 'knowledge' | 'profile';
  route: string;
  apiNamespace: string;
};

export const productModules: ProductModule[] = [
  { id: 'conversation', route: '/conversation', apiNamespace: '/conversations' },
  { id: 'writing', route: '/writing', apiNamespace: '/tasks/survey' },
  { id: 'tasks', route: '/tasks', apiNamespace: '/tasks' },
  { id: 'knowledge', route: '/knowledge', apiNamespace: '/knowledge' },
  { id: 'profile', route: '/profile', apiNamespace: '/settings' },
];
