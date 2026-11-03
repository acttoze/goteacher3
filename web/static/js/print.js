// 일정 인쇄(H6): 틀은 @media print가 맡는다. 단추는 브라우저 인쇄 창만 연다.
document.getElementById('printBtn').addEventListener('click', function () { window.print(); });
