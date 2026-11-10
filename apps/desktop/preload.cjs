const { contextBridge, ipcRenderer } = require('electron');

// 只暴露方法，不暴露 ipcRenderer 本体
contextBridge.exposeInMainWorld('desktop', {
  version: () => ipcRenderer.invoke('app:version'),
  notify: (title, body) => ipcRenderer.invoke('app:notify', { title, body }),
  apiBase: process.argv.find(value => value.startsWith('--aiecom-api-base='))?.slice('--aiecom-api-base='.length) || 'http://localhost:8000',
  isDesktop: true
});
