const workspace = process.env.MICROVM_WORKSPACE || "unknown";
const instance = process.env.MICROVM_INSTANCE_ID || "unknown";

console.log(JSON.stringify({
  message: "Mixed workspace worker started after the API dependency",
  workspace,
  instance
}));

setInterval(() => {
  console.log(JSON.stringify({
    event: "mixed-heartbeat",
    workspace,
    instance,
    timestamp: new Date().toISOString()
  }));
}, 5000);
