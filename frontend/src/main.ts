import { mount } from 'svelte'
import '@fontsource-variable/bricolage-grotesque'
import '@fontsource-variable/figtree'
import '@fontsource-variable/jetbrains-mono'
import './theme-tokens.css'
import './theme.css'
import './app.css'
import App from './App.svelte'

const app = mount(App, {
  target: document.getElementById('app')!,
})

export default app
