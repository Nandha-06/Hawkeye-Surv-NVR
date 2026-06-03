import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig({
	plugins: [
		tailwindcss(),
		sveltekit()
	],
	server: {
		proxy: {
			'/api/v1': {
				target: 'http://127.0.0.1:8080',
				changeOrigin: true
			},
			'/api/ws': {
				target: 'ws://127.0.0.1:8080',
				ws: true
			},
			'/ws': {
				target: 'ws://127.0.0.1:8080',
				ws: true
			}
		}
	}
});

