<?php
// One-time administrator fixture for a NEW local OpenMES database, not an adapter route.
// Uses upstream models and production services. Distributed under AGPL-3.0-or-later.
require '/var/www/html/vendor/autoload.php';
$app = require '/var/www/html/bootstrap/app.php';
$app->make(Illuminate\Contracts\Console\Kernel::class)->bootstrap();
$input = json_decode(stream_get_contents(STDIN), true, 512, JSON_THROW_ON_ERROR);
if (App\Models\WorkOrder::withoutGlobalScopes()->exists() || App\Models\User::where('username', 'knowledge-reader')->exists()) {
    throw new RuntimeException('Use a fresh independent demo database; existing records are preserved.');
}
$receipt = Illuminate\Support\Facades\DB::transaction(function () use ($input) {
    $admin = App\Models\User::where('username', 'admin')->firstOrFail();
    $admin->tenant_id = 1; $admin->save();
    Illuminate\Support\Facades\Auth::login($admin);
    app(App\Support\TenantContext::class)->set(1);
    $line = App\Models\Line::create(['code'=>'HC-L01','name'=>'演示机加工线','is_active'=>true]);
    $other = App\Models\Line::create(['code'=>'OTHER-L01','name'=>'未授权产线','is_active'=>true]);
    $product = App\Models\ProductType::create(['code'=>'HC-A17','name'=>'演示轴套','is_active'=>true]);
    $template = App\Models\ProcessTemplate::create(['product_type_id'=>$product->id,'name'=>'HC-A17 工艺路线','version'=>1,'is_active'=>true]);
    foreach ([1=>'OP-20 机加工',2=>'OP-30 尺寸检验'] as $number=>$name) {
        App\Models\TemplateStep::create(['process_template_id'=>$template->id,'step_number'=>$number,'name'=>$name,'kind'=>'production']);
    }
    $reader = App\Models\User::create(['name'=>'Knowledge integration reader','username'=>'knowledge-reader',
        'email'=>'knowledge-reader@openmes.local','password'=>bcrypt($input['reader_password']),
        'tenant_id'=>1,'force_password_change'=>false,'email_verified_at'=>now()]);
    $reader->assignRole('Operator'); $reader->lines()->sync([$line->id]);
    $token=$reader->createToken('knowledge-read-only',['knowledge:read-only']);
    $service=app(App\Services\WorkOrder\WorkOrderService::class);
    $orders=[];
    foreach (['MES-WO-STARTED','MES-WO-NOTSTARTED','MES-WO-OTHER'] as $number) {
        $order=$service->createWorkOrder(['order_no'=>$number,'line_id'=>$number==='MES-WO-OTHER'?$other->id:$line->id,
            'product_type_id'=>$product->id,'planned_qty'=>20]);
        $service->applyTransition($order,'accept');
        $batches=[];
        foreach ([1,2] as $i) {
            $batch=$service->createBatch($order,10,null,$number.'-B'.$i);
            if ($number==='MES-WO-STARTED' && $i===1) {
                Carbon\Carbon::setTestNow(Carbon\Carbon::parse('2026-11-09 08:10:00','Asia/Shanghai'));
                app(App\Services\WorkOrder\BatchService::class)->startStep($batch->steps()->orderBy('step_number')->firstOrFail(),$admin);
                Carbon\Carbon::setTestNow();
            }
            $batches[]=['id'=>$batch->id,'steps'=>$batch->steps()->orderBy('step_number')->pluck('id')->all()];
        }
        $orders[$number]=['id'=>$order->id,'batches'=>$batches];
    }
    return ['source_tenant_id'=>1,'reader_id'=>$reader->id,'line_id'=>$line->id,'other_line_id'=>$other->id,
        'orders'=>$orders,'api_token'=>$token->plainTextToken,'demo_only'=>true];
});
echo json_encode($receipt, JSON_THROW_ON_ERROR|JSON_UNESCAPED_UNICODE);
