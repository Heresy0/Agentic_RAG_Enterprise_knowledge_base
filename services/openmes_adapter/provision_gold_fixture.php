<?php
// AGPL-3.0-or-later. Administrator-only seed for a NEW isolated OpenMES clone.
// Never called by the read-only adapter or any model tool.
require '/var/www/html/vendor/autoload.php';
$app = require '/var/www/html/bootstrap/app.php';
$app->make(Illuminate\Contracts\Console\Kernel::class)->bootstrap();
if (getenv('GOLD100_FIXTURE_ALLOWED') !== '1' ||
    !str_starts_with((string)config('database.connections.pgsql.host'), 'gold100-')) {
    throw new RuntimeException('This command requires the dedicated gold100 database host.');
}
$input = json_decode(stream_get_contents(STDIN), true, 512, JSON_THROW_ON_ERROR);
if (App\Models\WorkOrder::withoutGlobalScopes()->where('order_no', 'like', 'DEMO-%')->exists()) {
    throw new RuntimeException('Fixture already exists; preserve it rather than reseeding.');
}
$receipt = Illuminate\Support\Facades\DB::transaction(function () use ($input) {
    $admin = App\Models\User::where('username', 'admin')->firstOrFail();
    Illuminate\Support\Facades\Auth::login($admin);
    app(App\Support\TenantContext::class)->set(1);
    $line = App\Models\Line::where('code', 'HC-L01')->firstOrFail();
    $service = app(App\Services\WorkOrder\WorkOrderService::class);
    $batchService = app(App\Services\WorkOrder\BatchService::class);
    $orders = [];
    foreach ($input['products'] as $p) {
        $product = App\Models\ProductType::create(['code'=>$p['model'], 'name'=>$p['name'], 'is_active'=>true]);
        $template = App\Models\ProcessTemplate::create(['product_type_id'=>$product->id,
            'name'=>$p['model'].' 合成验证路线', 'version'=>2, 'is_active'=>true]);
        foreach ($p['ops'] as $i=>$op) {
            App\Models\TemplateStep::create(['process_template_id'=>$template->id,
                'step_number'=>$i+1, 'name'=>$op['code'].' '.$op['name'], 'kind'=>'production']);
        }
        $names = [$p['work_order']];
        if ($p['family']==='B21') { $names[]='DEMO-GOLD-CONTROL'; }
        foreach ($names as $name) {
            Carbon\Carbon::setTestNow(Carbon\Carbon::parse($p['date'].' 09:00:00', 'Asia/Shanghai'));
            $order=$service->createWorkOrder(['order_no'=>$name, 'line_id'=>$line->id,
                'product_type_id'=>$product->id, 'planned_qty'=>$p['quantity']]);
            $service->applyTransition($order,'accept');
            $batches=[];
            for ($i=1; $i<=2; $i++) {
                $batch=$service->createBatch($order, $p['quantity']/2, null, $name.'-B'.$i);
                if ($i===1) {
                    $step=$batch->steps()->orderBy('step_number')->firstOrFail();
                    $batchService->startStep($step,$admin);
                    // Explicit synthetic fixture timestamp: batch 09:00, step 09:06.
                    // This does not assert a physical operation took place.
                    Carbon\Carbon::setTestNow(Carbon\Carbon::parse($p['date'].' 09:06:00','Asia/Shanghai'));
                    $step->update(['started_at'=>now()]);
                }
                $batches[]=['id'=>$batch->id,'steps'=>$batch->steps()->orderBy('step_number')->pluck('id')->all()];
            }
            $orders[$name]=['id'=>$order->id,'product_model'=>$p['model'],'batches'=>$batches];
            Carbon\Carbon::setTestNow();
        }
    }
    // Private controller credential for deliberate faults in this clone only.
    // Platform/adapter continues using its separate knowledge:read-only token.
    $control=$admin->createToken('gold100-fixture-controller',['*']);
    return ['orders'=>$orders,'controller_token'=>$control->plainTextToken,
        'synthetic_fixture'=>true,'factory_execution_evidence'=>false];
});
echo json_encode($receipt, JSON_THROW_ON_ERROR|JSON_UNESCAPED_UNICODE);
