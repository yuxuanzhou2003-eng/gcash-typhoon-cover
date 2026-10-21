import React from 'react';
import ReactDOM from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import '@fontsource/plus-jakarta-sans/400.css';
import '@fontsource/plus-jakarta-sans/500.css';
import '@fontsource/plus-jakarta-sans/600.css';
import '@fontsource/plus-jakarta-sans/700.css';
import '@fontsource/plus-jakarta-sans/800.css';
import App from './App';
import { demoMode } from './api';
import './styles.css';
import './climada-theme.css';

const client = new QueryClient({ defaultOptions: { queries: { retry: 1, staleTime: 1000, refetchOnWindowFocus: true }, mutations: { retry: false } } });
async function start() {
  if (demoMode) {
    const { worker, startMockScheduler } = await import('./mocks/browser');
    await worker.start({ onUnhandledRequest: 'bypass', quiet: true });
    startMockScheduler();
  }
  ReactDOM.createRoot(document.getElementById('root')!).render(<React.StrictMode><QueryClientProvider client={client}><App /></QueryClientProvider></React.StrictMode>);
}
start().catch(error => {
  const node = document.getElementById('root')!;
  node.textContent = `Unable to start the workbench: ${error instanceof Error ? error.message : 'unknown error'}. Please reload.`;
});
