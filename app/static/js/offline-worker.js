/* Saves music and videos for offline into this site's private storage on the device (OPFS), one at a time, in 8 MB
   pieces asked for with Range, written straight into the file: a download that breaks off (the network, the app
   closed) carries on from where it stopped. A worker, because only workers can write a file in place
   (createSyncAccessHandle), and that's also the way Safari has. Messages in: { file, url, headers } to save,
   { drop: file } to stop and delete; `chunk` sets the piece size. Out: { file, have, size, type } as it goes, then { file, done } or { file, error }. */
const CHUNK = 8 << 20;
const jobs = [], dropped = new Set();
let busy = false;

onmessage = ({ data }) => {
  if (data.drop) return dropped.add(data.drop);
  dropped.delete(data.file);
  jobs.push(data);
  if (!busy) work();
};

async function work() {
  busy = true;
  while (jobs.length) {
    const job = jobs.shift();
    if (dropped.has(job.file)) continue;
    try {
      // one window at a time saves a file (the app and a browser tab both may try): the other leaves it be
      const r = navigator.locks ? await navigator.locks.request(`parlor-offline:${job.file}`, { ifAvailable: true }, (lock) => (lock ? save(job) : { busy: true })) : await save(job);
      postMessage({ file: job.file, ...r });
    } catch (err) {
      postMessage({ file: job.file, error: err.name === "QuotaExceededError" ? "Not enough space on this device" : err.name === "NoModificationAllowedError" ? "Saving in another window" : err.message || String(err) });
    }
  }
  busy = false;
}

async function save({ file, url, headers = {}, chunk = CHUNK, size: known = 0 }) {
  const dir = await (await navigator.storage.getDirectory()).getDirectoryHandle("offline", { create: true });
  const h = await (await dir.getFileHandle(file, { create: true })).createSyncAccessHandle();
  let gone = false;
  try {
    let have = h.getSize();
    for (;;) {
      if (dropped.has(file)) { gone = true; return { dropped: true }; }
      const res = await fetch(url, { headers: { ...headers, Range: `bytes=${have}-${have + chunk - 1}` }, cache: "no-store" });
      const type = (res.headers.get("content-type") || "").split(/[;,]/)[0].trim();
      if (res.status === 416 && have) {
        if (!known || have === known) return { done: true, size: have, type }; // it was all here already
        h.truncate(0); // shorter on the server now: start again
        have = known = 0;
        continue;
      }
      if (!res.ok) throw new Error(res.status === 401 ? "Sign in again to save it" : res.status === 403 ? "Your account can't open this" : `The server answered ${res.status}`);
      if (res.status === 200) { // the server sent it whole: write it from the top as it comes
        h.truncate(0);
        have = 0;
        const size = +res.headers.get("content-length") || 0, reader = res.body.getReader();
        for (let shown = 0; ;) {
          const { done, value } = await reader.read();
          if (done) break;
          if (dropped.has(file)) { reader.cancel(); gone = true; return { dropped: true }; }
          h.write(value, { at: have });
          have += value.byteLength;
          if (have - shown >= CHUNK) { shown = have; postMessage({ file, have, size, type }); }
        }
        h.flush();
        return { done: true, size: have, type };
      }
      const piece = new Uint8Array(await res.arrayBuffer()), size = +(res.headers.get("content-range") || "").split("/")[1] || 0;
      if (have && known && size && size !== known) { // the file changed on the server since the first piece: start again
        h.truncate(0);
        have = 0;
        known = size;
        continue;
      }
      known ||= size;
      h.write(piece, { at: have });
      h.flush();
      have += piece.byteLength;
      postMessage({ file, have, size, type });
      if (!piece.byteLength || (size && have >= size)) return { done: true, size: have, type };
    }
  } finally {
    h.close();
    if (gone) await dir.removeEntry(file).catch(() => {});
  }
}
