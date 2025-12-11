# Session 生命周期管理重构设计

> **状态**: ✅ 已实现
> **实现日期**: 2024-12-11

## 1. 问题描述

### 1.1 现象

在长时间运行的 agent_chat 服务中，每次 WebSocket 连接断开后，内存不会被释放：

- 50 次连接后：189MB → 255MB（+66MB）
- 100 次连接后：227MB → 432MB（+205MB）

诊断显示大量对象未被 GC 回收：
- ControlBus: 41 个（应该是 0）
- DAGNode: 328 个（应该是 0）
- Station: 374 个（应该是 0）
- Logger: 1020+ 个

### 1.2 根本原因

当前架构中，Session 资源被**多个长生命周期的字典**持有：

```
SessionManager（长生命周期）
├── _sessions: Dict[str, Task]
├── _control_buses: Dict[str, ControlBus]
├── _dags: Dict[str, CompiledDAG]
├── _input_stations: Dict[str, Station]
└── _interrupt_tasks: Dict[str, Task]

TransportBase（长生命周期）
├── _read_queues: Dict[str, Queue]
├── _send_queues: Dict[str, Queue]
├── _priority_queues: Dict[str, Queue]
├── _video_queues: Dict[str, Queue]
├── _read_workers: Dict[str, Task]
├── _send_workers: Dict[str, Task]
├── _output_controllers: Dict[str, Any]
└── _control_buses: Dict[str, ControlBus]
```

问题：
1. **引用分散**：资源散落在 10+ 个字典中，清理时容易遗漏
2. **清理路径多**：正常退出、异常退出、disconnect 各有不同路径
3. **循环引用**：Station ↔ ControlBus ↔ DAG 之间相互引用

### 1.3 为什么打补丁无法解决

每次修复都是在某个路径上添加清理代码，但：
- 路径太多，容易遗漏
- 新功能可能引入新的引用
- 代码维护成本高

---

## 2. 设计目标

1. **单一所有权**：Session 的所有资源由一个对象持有
2. **生命周期绑定**：资源生命周期与 WebSocket 连接绑定
3. **自动清理**：使用 Python 语法（`async with`）保证清理
4. **无外部强引用**：长生命周期对象不持有 Session 资源的强引用

---

## 3. 架构设计

### 3.1 核心概念：SessionContext

将所有 Session 资源封装在一个 `SessionContext` 对象中：

```
SessionContext（与连接同生命周期）
├── session_id: str
├── control_bus: ControlBus
├── dag: CompiledDAG
├── input_station: InputStation
├── output_station: OutputStation
├── queues: SessionQueues
│   ├── read: Queue
│   ├── send: Queue
│   ├── priority: Queue
│   └── video: Queue
├── workers: SessionWorkers
│   ├── read_worker: Task
│   ├── send_worker: Task
│   └── dag_task: Task
└── state: SessionState
```

### 3.2 生命周期管理

```python
# WebSocket Handler 中
async def _handle_websocket(self, websocket: WebSocket, token: str = None):
    session_id = str(uuid.uuid4())
    
    # SessionContext 的生命周期与 async with 块绑定
    async with SessionContext.create(
        session_id=session_id,
        dag_factory=self.dag_factory,
        transport=self,
    ) as session:
        await session.run()
    
    # async with 退出时，无论正常还是异常，都会调用 __aexit__
    # SessionContext 被销毁，所有资源自动释放
```

### 3.3 对象关系

```
┌─────────────────────────────────────────────────────────────┐
│                    WebSocket Coroutine                       │
│                    (ASGI 管理的生命周期)                      │
│                                                              │
│   ┌──────────────────────────────────────────────────────┐  │
│   │            async with SessionContext:                 │  │
│   │                                                       │  │
│   │   SessionContext (唯一所有者)                         │  │
│   │   ├── ControlBus (强引用)                            │  │
│   │   ├── CompiledDAG (强引用)                           │  │
│   │   │   └── Stations (强引用)                          │  │
│   │   ├── InputStation (强引用)                          │  │
│   │   ├── Queues (强引用)                                │  │
│   │   └── Workers (强引用)                               │  │
│   │                                                       │  │
│   │   当 async with 退出时:                               │  │
│   │   1. __aexit__ 被调用                                │  │
│   │   2. 所有 worker 被取消                              │  │
│   │   3. 所有资源被释放                                   │  │
│   │   4. SessionContext 被销毁                           │  │
│   │   5. GC 回收所有对象                                 │  │
│   └──────────────────────────────────────────────────────┘  │
│                                                              │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│               长生命周期对象 (Transport, etc.)               │
│                                                              │
│   只持有：                                                   │
│   - WebSocket 连接引用（必需）                               │
│   - 配置信息（静态）                                         │
│                                                              │
│   不再持有：                                                 │
│   - Session 相关的任何资源                                   │
│   - 不再有 _sessions, _control_buses 等字典                 │
└─────────────────────────────────────────────────────────────┘
```

---

## 4. 接口设计

### 4.1 SessionContext

```python
@dataclass
class SessionQueues:
    """Session 的所有队列"""
    read: asyncio.Queue
    send: asyncio.Queue
    priority: asyncio.Queue
    video: Optional[asyncio.Queue] = None


@dataclass
class SessionWorkers:
    """Session 的所有 worker tasks"""
    read_worker: Optional[asyncio.Task] = None
    send_worker: Optional[asyncio.Task] = None
    dag_task: Optional[asyncio.Task] = None
    interrupt_task: Optional[asyncio.Task] = None


class SessionContext:
    """
    Session 上下文 - 封装单个连接的所有资源。
    
    设计原则：
    1. 单一所有者：所有 session 资源都由此对象持有
    2. 生命周期绑定：使用 async with 确保资源释放
    3. 无外部引用：长生命周期对象不持有此对象的强引用
    """
    
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.logger = logger.bind(session_id=session_id[:8])
        
        # 核心组件
        self.control_bus: Optional[ControlBus] = None
        self.dag: Optional[CompiledDAG] = None
        self.input_station: Optional[Station] = None
        
        # 队列和 workers
        self.queues: Optional[SessionQueues] = None
        self.workers: SessionWorkers = SessionWorkers()
        
        # 状态
        self._running: bool = False
        self._cleanup_done: bool = False
    
    @classmethod
    async def create(
        cls,
        session_id: str,
        dag_factory: Callable[[], Awaitable[DAG]],
        transport: "TransportBase",
    ) -> "SessionContext":
        """
        工厂方法：创建完整初始化的 SessionContext。
        
        Args:
            session_id: 会话 ID
            dag_factory: DAG 工厂函数
            transport: Transport 实例（用于创建 stations）
        
        Returns:
            完整初始化的 SessionContext
        """
        ctx = cls(session_id)
        
        # 创建队列
        ctx.queues = SessionQueues(
            read=asyncio.Queue(),
            send=asyncio.Queue(),
            priority=asyncio.Queue(),
            video=asyncio.Queue(),
        )
        
        # 创建 ControlBus
        ctx.control_bus = ControlBus()
        
        # 创建 DAG
        dag = await dag_factory()
        
        # 创建 Input/Output Stations（使用 context 的队列）
        ctx.input_station = transport.create_input_station(
            session_id=session_id,
            read_queue=ctx.queues.read,
            video_queue=ctx.queues.video,
        )
        
        output_station = transport.create_output_station(
            session_id=session_id,
            send_queue=ctx.queues.send,
            priority_queue=ctx.queues.priority,
        )
        
        # 编译 DAG
        dag.add_node("transport_out", output_station)
        ctx.dag = dag.compile(control_bus=ctx.control_bus)
        ctx.dag.set_session_id(session_id)
        
        # 设置 input_station
        ctx.input_station.control_bus = ctx.control_bus
        ctx.input_station.set_session_id(session_id)
        
        ctx.logger.info("SessionContext created")
        return ctx
    
    async def run(self) -> None:
        """
        运行 session（启动 DAG 和 workers）。
        
        此方法会阻塞直到 session 结束（正常完成或被取消）。
        """
        self._running = True
        
        try:
            # 启动 interrupt handler
            self.workers.interrupt_task = asyncio.create_task(
                self._handle_interrupts(),
                name=f"interrupt-{self.session_id[:8]}"
            )
            
            # 运行 DAG
            input_stream = self.input_station._generate_chunks()
            async for _ in self.dag.run(input_stream):
                pass
            
            self.logger.info("Session completed normally")
            
        except asyncio.CancelledError:
            self.logger.info("Session cancelled")
            if self.dag:
                self.dag.stop()
            raise
        
        except Exception as e:
            self.logger.error(f"Session error: {e}", exc_info=True)
            raise
    
    async def _handle_interrupts(self) -> None:
        """处理中断信号"""
        try:
            while self._running:
                interrupt = await self.control_bus.receive_interrupt()
                if interrupt and self.dag:
                    await self.dag.handle_interrupt(interrupt)
        except asyncio.CancelledError:
            pass
    
    async def cleanup(self) -> None:
        """
        清理所有资源。
        
        此方法是幂等的，可以安全地多次调用。
        """
        if self._cleanup_done:
            return
        
        self._cleanup_done = True
        self._running = False
        self.logger.info("Cleaning up session...")
        
        # 1. 取消所有 workers
        for task in [
            self.workers.dag_task,
            self.workers.interrupt_task,
            self.workers.read_worker,
            self.workers.send_worker,
        ]:
            if task and not task.done():
                task.cancel()
                try:
                    await asyncio.wait_for(task, timeout=2.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
        
        # 2. 清理 DAG（调用各 station 的 cleanup）
        if self.dag:
            await self.dag.cleanup()
        
        # 3. 清空队列
        if self.queues:
            for queue in [
                self.queues.read,
                self.queues.send,
                self.queues.priority,
                self.queues.video,
            ]:
                if queue:
                    while not queue.empty():
                        try:
                            queue.get_nowait()
                        except asyncio.QueueEmpty:
                            break
        
        # 4. 清除所有引用（帮助 GC）
        self.dag = None
        self.input_station = None
        self.control_bus = None
        self.queues = None
        self.workers = SessionWorkers()
        
        self.logger.info("Session cleaned up")
    
    async def __aenter__(self) -> "SessionContext":
        """进入 async with 块"""
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """退出 async with 块 - 确保清理"""
        await self.cleanup()
```

### 4.2 Transport 接口变更

```python
class TransportBase(ABC):
    """
    Transport 基类 - 不再持有 session 资源。
    
    变更：
    - 移除所有 session 相关的字典
    - 提供工厂方法创建 stations
    - WebSocket 管理保留
    """
    
    def __init__(self, name: str = "Transport"):
        self._name = name
        self.logger = logger.bind(component=name)
        
        # 只保留 WebSocket 连接管理
        self._connections: Dict[str, WebSocket] = {}
        
        # 配置（静态）
        self._protocol: Optional[ProtocolBase] = None
        self._latency_monitor: Optional[Any] = None
        
        # 运行状态
        self._running = False
    
    @abstractmethod
    def create_input_station(
        self,
        session_id: str,
        read_queue: asyncio.Queue,
        video_queue: Optional[asyncio.Queue] = None,
    ) -> "Station":
        """
        工厂方法：创建 InputStation。
        
        由 SessionContext 调用，Station 由 SessionContext 持有。
        """
        pass
    
    @abstractmethod
    def create_output_station(
        self,
        session_id: str,
        send_queue: asyncio.Queue,
        priority_queue: asyncio.Queue,
    ) -> "Station":
        """
        工厂方法：创建 OutputStation。
        
        由 SessionContext 调用，Station 由 SessionContext 持有。
        """
        pass
    
    async def _handle_websocket(
        self,
        websocket: WebSocket,
        token: Optional[str] = None
    ) -> None:
        """
        处理 WebSocket 连接。
        
        关键变更：使用 async with 管理 SessionContext 生命周期。
        """
        session_id = str(uuid.uuid4())
        
        try:
            await websocket.accept()
            self._connections[session_id] = websocket
            
            # 使用 async with 确保资源释放
            async with SessionContext.create(
                session_id=session_id,
                dag_factory=self._dag_factory,
                transport=self,
            ) as session:
                # 启动 workers
                await self._start_workers(session_id, session)
                
                # 运行 session
                await session.run()
            
            # async with 退出，资源自动释放
            
        except WebSocketDisconnect:
            self.logger.info(f"WebSocket disconnected: {session_id}")
        except Exception as e:
            self.logger.error(f"WebSocket error: {e}", exc_info=True)
        finally:
            self._connections.pop(session_id, None)
```

### 4.3 SessionManager 简化

```python
class SessionManager:
    """
    Session 管理器 - 简化版。
    
    变更：
    - 不再持有 session 资源字典
    - 只负责 Transport 启动/停止
    - SessionContext 由 Transport 内部管理
    """
    
    def __init__(
        self,
        transport: TransportBase,
        dag_factory: Callable[[], DAG],
    ):
        self.transport = transport
        self.dag_factory = dag_factory
        self.logger = logger.bind(component="SessionManager")
        
        # 将 dag_factory 传递给 transport
        self.transport.set_dag_factory(dag_factory)
    
    async def start(self) -> None:
        """启动服务"""
        self.logger.info("Starting session manager...")
        await self.transport.start()
        self.logger.info("Session manager started")
    
    async def stop(self) -> None:
        """停止服务"""
        self.logger.info("Stopping session manager...")
        await self.transport.stop()
        self.logger.info("Session manager stopped")
```

---

## 5. 数据流

### 5.1 连接建立

```
1. Client connects to WebSocket
   │
2. Transport._handle_websocket() called
   │
3. async with SessionContext.create(...) as session:
   │
   ├── 创建 Queues（由 SessionContext 持有）
   ├── 创建 ControlBus（由 SessionContext 持有）
   ├── 创建 DAG + Stations（由 SessionContext 持有）
   └── 启动 Workers（由 SessionContext 管理）
   │
4. await session.run()
   │
   └── DAG 运行，处理音频流
```

### 5.2 连接断开

```
1. Client disconnects / Error occurs
   │
2. async with 块退出（正常或异常）
   │
3. SessionContext.__aexit__() 被调用
   │
4. SessionContext.cleanup() 执行：
   │
   ├── 取消所有 workers
   ├── 清理 DAG 和 Stations
   ├── 清空 Queues
   └── 清除所有引用
   │
5. SessionContext 对象被销毁
   │
6. Python GC 回收所有资源
   │
7. 内存释放
```

---

## 6. 迁移计划

### Phase 1：准备工作

1. 创建 `SessionContext` 类
2. 创建 `SessionQueues` 和 `SessionWorkers` 数据类
3. 添加 Transport 的工厂方法 `create_input_station` / `create_output_station`

### Phase 2：Transport 改造

1. 修改 `_handle_websocket` 使用 `async with SessionContext`
2. 移除 Transport 中的 session 相关字典
3. Workers 的启动/停止逻辑移入 SessionContext

### Phase 3：SessionManager 简化

1. 移除 SessionManager 中的 session 相关字典
2. 简化 SessionManager 为仅管理 Transport 生命周期

### Phase 4：测试验证

1. 单元测试：SessionContext 生命周期
2. 集成测试：多连接并发
3. 压力测试：100+ 连接后内存检查

---

## 7. 兼容性考虑

### 7.1 向后兼容

- 保留 `SessionManager` 接口，内部实现变更
- 保留 `Transport.start()` / `Transport.stop()` 接口

### 7.2 破坏性变更

- 移除 `SessionManager._sessions` 等字典（内部实现）
- 移除 `Transport._control_buses` 等字典（内部实现）
- 外部代码如果直接访问这些字典将失效

---

## 8. 风险评估

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| 重构范围大 | 可能引入 bug | 分阶段实施，充分测试 |
| Workers 管理变更 | 可能影响性能 | 保持现有逻辑，只改归属 |
| 现有测试可能失效 | 测试需要更新 | 更新测试同步进行 |

---

## 9. 成功标准

1. **内存稳定**：100 次连接后内存增长 < 10MB
2. **对象回收**：所有 session 对象被正确 GC
3. **功能正常**：所有现有功能不受影响
4. **代码简化**：移除 10+ 个 session 相关字典

---

## 10. 总结

通过将 Session 资源封装在 `SessionContext` 中，并使用 `async with` 管理其生命周期，可以从根本上解决内存泄漏问题：

1. **单一所有权**：所有资源由 SessionContext 持有
2. **生命周期绑定**：资源与 WebSocket 连接同生死
3. **自动清理**：Python 语法保证 `__aexit__` 被调用
4. **无需打补丁**：新功能不会引入新的泄漏

这种设计符合 Python 的最佳实践，也是其他成熟框架（如 aiohttp, FastAPI dependencies）采用的模式。
