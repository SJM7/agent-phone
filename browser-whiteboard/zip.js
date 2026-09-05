/* ZIP (stored entries): dependency-free, readable by Finder and standard unzip. */
(() => {
  const table = Array.from({ length: 256 }, (_, n) => {
    for (let i = 0; i < 8; i++) n = n & 1 ? 0xedb88320 ^ (n >>> 1) : n >>> 1;
    return n >>> 0;
  });
  function crc(bytes) { let n = 0xffffffff; for (const b of bytes) n = table[(n ^ b) & 255] ^ (n >>> 8); return (n ^ 0xffffffff) >>> 0; }
  const header = size => { const bytes = new Uint8Array(size); return { bytes, view: new DataView(bytes.buffer) }; };
  async function zip(files) {
    const chunks = [], directory = []; let offset = 0, directorySize = 0;
    for (const file of files) {
      const name = new TextEncoder().encode(file.name);
      const data = file.data instanceof Blob ? new Uint8Array(await file.data.arrayBuffer()) : new TextEncoder().encode(file.data);
      const checksum = crc(data);
      const local = header(30);
      local.view.setUint32(0, 0x04034b50, true); local.view.setUint16(4, 20, true); local.view.setUint16(6, 0x800, true);
      local.view.setUint32(14, checksum, true); local.view.setUint32(18, data.length, true); local.view.setUint32(22, data.length, true); local.view.setUint16(26, name.length, true);
      chunks.push(local.bytes, name, data);
      const central = header(46);
      central.view.setUint32(0, 0x02014b50, true); central.view.setUint16(4, 20, true); central.view.setUint16(6, 20, true); central.view.setUint16(8, 0x800, true);
      central.view.setUint32(16, checksum, true); central.view.setUint32(20, data.length, true); central.view.setUint32(24, data.length, true); central.view.setUint16(28, name.length, true); central.view.setUint32(42, offset, true);
      directory.push(central.bytes, name); directorySize += central.bytes.length + name.length;
      offset += local.bytes.length + name.length + data.length;
    }
    const end = header(22);
    end.view.setUint32(0, 0x06054b50, true); end.view.setUint16(8, files.length, true); end.view.setUint16(10, files.length, true);
    end.view.setUint32(12, directorySize, true); end.view.setUint32(16, offset, true);
    return new Blob([...chunks, ...directory, end.bytes], { type: "application/zip" });
  }
  globalThis.WhiteboardZip = zip;
})();
