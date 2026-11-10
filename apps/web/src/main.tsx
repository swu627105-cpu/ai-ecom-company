import { createRoot } from 'react-dom/client';
import './app.css';
import App from './App';

// 会议状态机在服务端推进，这里不开 StrictMode，避免开发环境重复挂载导致 /next 被调两次
createRoot(document.getElementById('root')!).render(<App />);
