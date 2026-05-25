const workspace = process.env.MICROVM_WORKSPACE || "unknown";
const instance = process.env.MICROVM_INSTANCE_ID || "unknown";

console.log(JSON.stringify({
  message: "Hello from the MicroVM Node component",
  workspace,
  instance
}));

setInterval(() => {
  console.log(JSON.stringify({
    event: "heartbeat",
    workspace,
    instance,
    timestamp: new Date().toISOString()
  }));
}, 5000);
