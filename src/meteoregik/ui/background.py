"""Запуск долгой функции вне GUI-потока с доставкой результата обратно в
GUI-поток — чтобы интерфейс не "зависал" на выборке из БД и сборке
раскладки, а лоадер успевал отрисоваться и оставался живым (правка
2026-09-25, п.6).

Функция не должна трогать виджеты: только данные (запрос к БД через
Engine — SQLAlchemy даёт каждому потоку своё соединение из пула — и чистые
вычисления). Результат/исключение приходят сигналом объекта, живущего в
GUI-потоке, поэтому on_done/on_error выполняются уже в GUI-потоке
(queued-соединение), где с виджетами работать можно.
"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal


class _TaskSignals(QObject):
    finished = Signal(object)
    failed = Signal(object)


class _Task(QRunnable):
    def __init__(self, fn: Callable[[], Any], signals: _TaskSignals) -> None:
        super().__init__()
        self._fn = fn
        self._signals = signals

    def run(self) -> None:
        try:
            result = self._fn()
        except Exception as exc:  # noqa: BLE001 — любую ошибку отдаём в GUI-поток
            self._emit(self._signals.failed, exc)
        else:
            self._emit(self._signals.finished, result)

    @staticmethod
    def _emit(signal: Any, value: object) -> None:
        # Владелец (например, закрытый диалог) мог быть уже удалён вместе с
        # _TaskSignals — тогда результат просто никому не нужен.
        try:
            signal.emit(value)
        except RuntimeError:
            pass


def run_in_background(
    owner: QObject,
    fn: Callable[[], Any],
    on_done: Callable[[Any], None],
    on_error: Callable[[BaseException], None],
) -> None:
    """Выполняет fn в пуле потоков Qt; on_done(result) или on_error(exc) —
    в GUI-потоке. owner — родитель служебного объекта сигналов: при
    удалении owner (закрытии окна) обратные вызовы больше не придут."""
    signals = _TaskSignals(owner)
    signals.finished.connect(on_done)
    signals.failed.connect(on_error)
    signals.finished.connect(signals.deleteLater)
    signals.failed.connect(signals.deleteLater)
    QThreadPool.globalInstance().start(_Task(fn, signals))
