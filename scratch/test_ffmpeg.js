const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');

const filepath = path.join(__dirname, '..', '.data', 'recordings', 'webcam_local', 'rec_2026-05-31T18-15-40.924Z_bsep.webm');

console.log('Testing FFmpeg on file:', filepath);
console.log('File exists:', fs.existsSync(filepath));

// We try the standard arguments we used
const ffmpegArgs = [
    '-y',
    '-nostdin',
    '-i', filepath,
    '-c:v', 'libx264',
    '-preset', 'ultrafast',
    '-tune', 'zerolatency',
    '-c:a', 'aac',
    '-b:a', '128k',
    '-ac', '2',
    '-f', 'mpegts',
    'pipe:1'
];

console.log('Spawning FFmpeg with args:', ffmpegArgs.join(' '));
const child = spawn('ffmpeg', ffmpegArgs);

let stdoutBytes = 0;
let stderrData = '';

child.stdout.on('data', (chunk) => {
    stdoutBytes += chunk.length;
});

child.stderr.on('data', (chunk) => {
    stderrData += chunk.toString();
});

child.on('exit', (code) => {
    console.log('FFmpeg exited with code:', code);
    console.log('Transcoded MPEG-TS Bytes produced:', stdoutBytes);
    console.log('FFmpeg Stderr logs:\n', stderrData);
});

child.on('error', (err) => {
    console.error('Failed to spawn FFmpeg process:', err);
});
