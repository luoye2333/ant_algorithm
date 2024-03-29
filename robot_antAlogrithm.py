import numpy as np
import random
import math
import matplotlib.pyplot as plt
import cv2
import threading
from matplotlib.animation import FuncAnimation
import time
import datetime
import os

class ant:
    def __init__(self,
                 terrain_x,
                 terrain_y,
                 accuracy,
                 start_x_CoM=920.0,
                 start_y_CoM=424.3,
                 start_pitch=0,
                 target_x_CoM=2340,
                 target_y_CoM=924.3,
                 target_pitch=0,
                 end_threshold=5,
                 ):
        
        self.path=[]
        self.path.append([start_x_CoM,start_y_CoM,start_pitch])
        self.current_x_CoM=start_x_CoM
        self.current_y_CoM=start_y_CoM
        self.current_pitch=start_pitch
        self.target_x_CoM=target_x_CoM
        self.target_y_CoM=target_y_CoM
        self.target_pitch=target_pitch

        self.end_threshold=end_threshold

        self.terrain_x=terrain_x
        self.terrain_y=terrain_y
        self.accuracy=accuracy

        self.body_length=1040
        self.leg_length1=300
        self.leg_length2=300
        self.leg_num=4

        foot1=[400,0]
        foot2=[660,0]
        foot3=[1180,0]
        foot4=[1440,0]
        self.foot_path=[]
        self.foot_path.append([foot1,foot2,foot3,foot4])
        self.hip_path=[]
        self.hip_path.append(self.calculate_hip_position())

        self.path_length=0
        self.failed=False

    def calculatePathLength(self):
        #计算路径长度指标
        path_length=0
        for i in range(len(self.path)-1):
            point1=self.path[i]
            point2=self.path[i+1]
            pos1=np.array(point1[0:2])
            pos2=np.array(point2[0:2])
            path_length+=math.sqrt(np.sum(pow(pos1-pos2,2)))

        self.path_length=path_length


        #TODO:除了机身总和路径长度，还可以加其他指标
        #轨迹连续性
        #运动学约束余量

        return self.path_length
    
    def foot_GCS2BCS(self,
                     foot_pos_GCS,
                     hip_pos_GCS,
                     pitch=None):
        if pitch is None:
            pitch=self.path[-1][2]
        
        pitch=pitch/180*math.pi
        dx=foot_pos_GCS[0]-hip_pos_GCS[0]
        dy=foot_pos_GCS[1]-hip_pos_GCS[1]
        
        #HCS y轴向下
        HCS_x=dx*math.cos(pitch)+dy*math.sin(pitch)
        HCS_y=dx*math.sin(pitch)-dy*math.cos(pitch)

        return [HCS_x,HCS_y]

    def inverseKinematics(self,
                          foot_pos_GCS,
                          hip_pos_GCS,
                          leg_configuration=1):
        """
        返回关节角度(rad),-1左侧,1右侧
        """

        l1=self.leg_length1
        l2=self.leg_length2
        
        x,y=self.foot_GCS2BCS(foot_pos_GCS,hip_pos_GCS)
        dist=math.sqrt(x**2+y**2)
        try:
            alpha=math.acos((dist**2+l1**2-l2**2)/(2*dist*l1))
            beta=math.acos((l1**2+l2**2-dist**2)/(2*l2*l1))
        except:
            Exception("超出工作空间范围")
        phi=math.atan2(y,x)

        if leg_configuration==1:
            #右侧
            theta_thigh=phi-alpha
            theta_shank=-(beta-theta_thigh)
        elif leg_configuration==-1:
            #左侧
            theta_thigh=phi+alpha
            theta_shank=-(2*math.pi-beta-theta_thigh)
        else:
            Exception("关节方向未指定")

        return [theta_thigh,theta_shank]

    def calculate_knee_postion(self,
                               hip_point=None,
                               foot_point=None,
                               pitch=None,
                               leg_index=None):
        if hip_point is None:
            hip_point=self.hip_path[-1][leg_index]
            foot_point=self.foot_path[-1][leg_index]
        if pitch is None:
            pitch=self.path[-1][2]
        
        pitch=pitch/180*math.pi

        if leg_index<=1:
            leg_configuration=-1
        else:
            leg_configuration=1
        [theta_thigh,theta_shank]=self.inverseKinematics(foot_point,hip_point,leg_configuration)
        kx_HCS=self.leg_length1*math.cos(theta_thigh)
        ky_HCS=self.leg_length1*math.sin(theta_thigh)

        kx_GCS=hip_point[0]+kx_HCS*math.cos(pitch)+ky_HCS*math.sin(pitch)
        ky_GCS=hip_point[1]+kx_HCS*math.sin(pitch)-ky_HCS*math.cos(pitch)

        return [kx_GCS,ky_GCS]

    def obstacleDetection(self,
                          cx,
                          cy,
                          pitch,
                          foot_pos,
                          hip_pos,
                          leg_index=0):
        #检测有没有不符合运动学约束

        if leg_index<=1:
            leg_configuration=-1
        else:
            leg_configuration=1

        #比如落脚点超出范围
        #检测是否离髋关节超过2l就行
        foot_pos=np.array(foot_pos)
        hip_pos=np.array(hip_pos)
        hip_foot_dist=math.sqrt(np.sum(pow(foot_pos-hip_pos,2)))
        if hip_foot_dist>2*self.leg_length1:
            return True
        
        #限制髋关节必须在腿上方
        if hip_pos[1]<foot_pos[1]:
            return True
        #限制膝关节也必须在腿上方
        knee_pos=self.calculate_knee_postion(foot_point=foot_pos,
                                         hip_point=hip_pos,
                                         leg_index=leg_index,
                                         pitch=pitch)
        if knee_pos[1]<foot_pos[1]:
            return True
        #限制大腿角度在下半圈
        [theta_thigh,theta_shank]=self.inverseKinematics(foot_pos,hip_pos,leg_configuration)
        theta_thigh=theta_thigh*180/math.pi
        if (leg_configuration==1):
            if theta_thigh<0:
                return True
        elif leg_configuration==-1:
            if theta_thigh>180:
                return True

        return False

    def judgeDirection(self,
                       info_density_array,
                       info_density_negative_array):
        #TODO:逻辑需要整体更改
        #信息素位于一个广义上的点(x,y,pitch,foot)
        #而代表这个选择好或者坏
        #x\y而言，可以偏转速度方向使选择靠近信息素
        #pitch则不一定，可能连续变化pitch角度，有的可以有的不可以
        #foot就更加缺少连续性

        #逻辑可以改成随机生成一些备选方案，然后选择一个打分高的执行
        #单独的质心坐标不不代表什么，要和pitch,foot一起考虑

        #创建一个0~360度的数组
        #对圆形范围内的所有点进行投票
        gap=15#区间长度
        bar_pitch=np.zeros(int(360/gap))
        bar_CoM_angle=np.zeros(int(360/gap))
        #0gap~1gap,1gap~2gap,n-1~ngap
        #共360/gap长度
        #第i对应(i~i+1)*gap范围

        current_x,current_y,current_pitch=self.path[-1]
        current_all_foot=self.foot_path[-1]
        # density_array_concat=np.concatenate((info_density_array,info_density_negative_array))
        if len(info_density_array)==0 and\
            len(info_density_negative_array)==0:
            density_array_concat=[]
        elif len(info_density_array)==0:
            density_array_concat=info_density_negative_array
        elif len(info_density_negative_array)==0:
            density_array_concat=info_density_array
        else:
            density_array_concat=np.concatenate((info_density_array,
                                                info_density_negative_array))
        if len(density_array_concat)==0:
            return [None,None]
        
        for point_index in range(len(density_array_concat)):
            point=density_array_concat[point_index]
            [cx,cy,pitch,all_foot,density]=point
            dy=cy-current_y
            dx=cx-current_x

            CoM_angle=math.atan2(dy,dx)*180/math.pi
            if CoM_angle<0:CoM_angle+=360#-180~0转换到180~360
            index_CoM_angle=int(CoM_angle/gap)
            index_pitch_angle=int(pitch/gap)

            if density<0:
                # 如果density为负，表现为斥力
                # 旋转180度添加吸引力，防止softmax出错
                index_CoM_angle=index_CoM_angle+int(len(bar_pitch)/2)
                # index_pitch_angle=index_pitch_angle+int(len(bar_pitch)/2)
                index_CoM_angle=index_CoM_angle % len(bar_pitch)
                # index_pitch_angle=index_pitch_angle % len(bar_pitch)
                density=-density

            #计算相关度(信息素点到当前点的广义上的距离)
            #质心位置相关度、pitch相关度、落脚点相关度
            dist=math.sqrt((dx**2+dy**2))
            #dist=0,r=1
            #dist=alpha,r=0.5
            alpha1=1/50
            relativity_CoM=1/(1+alpha1*dist)

            pitch_delta=abs(current_pitch-pitch)
            alpha2=1/30
            relativity_pitch=1/(1+alpha2*pitch_delta)

            foot_dist=0
            for foot_index in range(self.leg_num):
                current_foot=np.array(current_all_foot[foot_index])
                foot=np.array(all_foot[foot_index])
                foot_dist+=math.sqrt(np.sum(pow(current_foot-foot,2)))
            alpha3=1/100
            relativity_foot=1/(1+alpha3*foot_dist)

            #指数权重,质心位置影响最大
            weight1=1;weight2=1;weight3=0.5
            relativity=pow(relativity_CoM,weight1)*\
                       pow(relativity_pitch,weight2)*\
                       pow(relativity_foot,weight3)
            if relativity<0.1:
                relativity=0
            
            bar_CoM_angle[index_CoM_angle]+=density*relativity
            bar_pitch[index_pitch_angle]+=density*relativity
        
        if ((np.max(bar_CoM_angle)==0) and (np.min(bar_CoM_angle)==0))or\
           ((np.max(bar_pitch)==0) and (np.min(bar_pitch)==0)):
            return [None,None]

        #得出最大区间
        #硬最大值
        #max_index=np.argmax(bar)

        #softmax
        bar_CoM_angle-=np.max(bar_CoM_angle)#防溢出
        prob_array=np.exp(bar_CoM_angle)/np.sum(np.exp(bar_CoM_angle))   
        max_index=chooseByProb(range(len(bar_CoM_angle)),prob_array)  
        CoM_info_angle=(max_index+0.5)*gap

        bar_pitch-=np.max(bar_pitch)#防溢出
        prob_array=np.exp(bar_pitch)/np.sum(np.exp(bar_pitch))   
        max_index=chooseByProb(range(len(bar_pitch)),prob_array)  
        pitch_info_angle=(max_index+0.5)*gap
        print("信息素pitch:{0},CoM:{1}".format(pitch_info_angle,CoM_info_angle))
        return [pitch_info_angle,CoM_info_angle]

    def walk(self,
             info_density_array,
             info_density_negative_array):
        #根据信息素，生成下一步
        global_count=0
        foot_try_limit=10
        body_try_limit=5

        while(True):
            global_count+=1
            print("body-try:{0}".format(global_count))
            # time.sleep(0.1)
            #如果距离终点比较近了,就指向终点
            cx=self.path[-1][0]
            cy=self.path[-1][1]
            dx=cx-self.target_x_CoM
            dy=cy-self.target_y_CoM
            target_dist=math.sqrt(dx**2+dy**2)
            goto_target=target_dist<200

            if goto_target:
                #朝终点走
                delta=self.target_pitch-self.current_pitch
                sgn=delta/abs(delta)
                step=2.5
                next_pitch_CoM=self.current_pitch+step*sgn
                CoM_walk_angle=math.atan2(-dy,-dx)
                
            else:
                case=2
                if case==1:
                    #策略1：随机走
                    next_pitch_CoM=self.current_pitch+(random.random()*5-2.5)
                    #假设只能向前走,-90~90
                    angle_down=-30
                    angle_up=60
                    random_angle=random.random()*(angle_up-angle_down)+angle_down
                    CoM_walk_angle=self.current_pitch+random_angle
                    CoM_walk_angle=CoM_walk_angle/180*math.pi

                if case==2:
                    #策略2：计算信息素强度决定反向
                    [pitch_info_angle,CoM_info_angle]=self.judgeDirection(info_density_array,
                                                                          info_density_negative_array)
                    if pitch_info_angle is not None:
                        #根据最大信息素角度产生偏转
                        delta=pitch_info_angle-self.current_pitch
                        if delta>180:delta-=360
                        if delta<-180:delta+=360
                        next_pitch_CoM=self.current_pitch+delta/15

                        #计算上一时刻CoM速度角度
                        if len(self.path)<2:
                            last_angle=0
                        else:
                            last_x=self.path[-2][0]
                            last_y=self.path[-2][1]
                            last_angle=math.atan2(cy-last_y,cx-last_x)/math.pi*180

                        delta=CoM_info_angle-last_angle
                        if delta>180:delta-=360
                        if delta<-180:delta+=360
                        CoM_walk_angle=last_angle+delta/15
                        CoM_walk_angle=CoM_walk_angle/180*math.pi
                    else:
                        #没有信息素，则随机
                        next_pitch_CoM=self.current_pitch+(random.random()*5-2.5)
                        angle_down=-30
                        angle_up=60
                        random_angle=random.random()*(angle_up-angle_down)+angle_down
                        CoM_walk_angle=self.current_pitch+random_angle
                        CoM_walk_angle=CoM_walk_angle/180*math.pi

            step_CoM=(random.random()+1)*20
            next_x_CoM=self.current_x_CoM+step_CoM*math.cos(CoM_walk_angle)
            next_y_CoM=self.current_y_CoM+step_CoM*math.sin(CoM_walk_angle)


            #生成四个落脚点
            #必须在地形上取点
            #获取当前位置在地形的x索引
            foot_current_pos=np.array(self.foot_path)[-1]
            foot_x=foot_current_pos[:,0]
            foot_current_index=(foot_x/self.accuracy).astype(int)
            next_foot_path=[]
            regenerate_flag=False
            #随机前进或后退几步accuracy距离
            for leg_index in range(self.leg_num):
                foot_step=random.randint(-5,25)
                foot_next_index=foot_current_index[leg_index]+foot_step
                foot_next_index=min(foot_next_index,len(self.terrain_x)-1)
                foot_next_index=max(foot_next_index,0)
                foot_next=[self.terrain_x[foot_next_index],
                           self.terrain_y[foot_next_index]]
                hip=self.calculate_hip_position(leg_index=leg_index,
                                                cx=next_x_CoM,
                                                cy=next_y_CoM,
                                                pitch=next_pitch_CoM)
                print("leg:{0}".format(leg_index))
                # time.sleep(0.1)
                try_count=0
                while self.obstacleDetection(cx=next_x_CoM,
                                             cy=next_y_CoM,
                                             pitch=next_pitch_CoM,
                                             foot_pos=foot_next,
                                             hip_pos=hip,
                                             leg_index=leg_index):
                    #重新生成落脚点
                    foot_step=random.randint(-5,5)
                    foot_next_index=foot_current_index[leg_index]+foot_step
                    foot_next_index=min(foot_next_index,len(self.terrain_x)-1)
                    foot_next_index=max(foot_next_index,0)
                    foot_next=[self.terrain_x[foot_next_index],
                               self.terrain_y[foot_next_index]]

                    try_count+=1
                    if try_count>foot_try_limit:
                        #有可能是因机身位置不好导致怎么生成都失败
                        #尝试很多次了以后应该重新生成机身
                        regenerate_flag=True
                        break
                if regenerate_flag:
                    break
                else:
                    next_foot_path.append(foot_next)
            
            if regenerate_flag:
                #失败，重新生成机身
                if (global_count<body_try_limit):
                    continue
                else:
                    #走进死胡同，生成失败
                    self.failed=True
                    break
            else:
                #生成成功
                self.current_x_CoM=next_x_CoM
                self.current_y_CoM=next_y_CoM
                self.current_pitch=next_pitch_CoM
                self.path.append([next_x_CoM,next_y_CoM,next_pitch_CoM])
                self.foot_path.append(next_foot_path)
                self.hip_path.append(self.calculate_hip_position(cx=next_x_CoM,
                                                                 cy=next_y_CoM,
                                                                 pitch=next_pitch_CoM))
                
                self.calculatePathLength()
                break
        return
    
    def isEnd(self):
        #如果离终点距离小于阈值，且姿态符合则停止
        dx=self.current_x_CoM-self.target_x_CoM
        dy=self.current_y_CoM-self.target_y_CoM
        dist=math.sqrt(dx**2+dy**2)
        case1=dist<self.end_threshold
        case2=(self.current_pitch-self.target_pitch)<5
        if (case1)and(case2):
            return True
        return False

    def calculate_hip_position(self,
                               cx=-1,
                               cy=-1,
                               pitch=-1,
                               leg_index=-1):
        '''
        根据质心位置(cx,cy)和俯仰角(pitch(rad))计算髋关节的坐标\\
        如果(cx,cy,pitch)传入-1,则默认取当前状态下的坐标\\
        leg_index:腿编号,传入-1同时返回四条腿的坐标
        '''
        if(cx==-1):
            cx=self.current_x_CoM
            cy=self.current_y_CoM
            pitch=self.current_pitch

        pitch=pitch/180*math.pi
        l=self.body_length

        hip1_x=cx-l/2*math.cos(pitch)
        hip1_y=cy-l/2*math.sin(pitch)
        hip1=[hip1_x,hip1_y]

        hip2_x=cx-l/4*math.cos(pitch)
        hip2_y=cy-l/4*math.sin(pitch)
        hip2=[hip2_x,hip2_y]
        
        hip3_x=cx+l/4*math.cos(pitch)
        hip3_y=cy+l/4*math.sin(pitch)
        hip3=[hip3_x,hip3_y]

        hip4_x=cx+l/2*math.cos(pitch)
        hip4_y=cy+l/2*math.sin(pitch)
        hip4=[hip4_x,hip4_y]
        
        hip=[hip1,hip2,hip3,hip4]

        if leg_index==0:
            return hip1
        elif leg_index==1:
            return hip2
        elif leg_index==2:
            return hip3
        elif leg_index==3:
            return hip4
        else:
            return hip
    
class antAlogorithm:
    def __init__(self,
                 ant_limit=500,
                 accuracy=1,
                 terrain_type=1,
                 plot=True,
                 history_max_length=10,
                 ):
        self.ant_limit=ant_limit
        self.accuracy=accuracy
        self.iteration_count=0
        self.terrain_type=terrain_type

        
        self.InitializeTerrain()

        # image_size_x=int(np.max(self.terrain_x)/accuracy)
        # image_size_y=int(np.max(self.terrain_y)/accuracy)
        image_size_x=int(np.max(self.terrain_x))
        image_size_y=int(np.max(self.terrain_y))+700

        self.plot=plot 
        if self.plot:
            fig,axes = plt.subplots()
            fig.set_size_inches([7,7]) # 设置图像大小
            axes.set_xlim(0, image_size_x)
            axes.set_ylim(0-200, image_size_y)
            self.fig=fig
            self.axes=axes

        
        terrain_image=np.zeros((image_size_x,image_size_y),dtype=np.uint8)
        terrain_image=cv2.transpose(terrain_image)

        for line in self.terrain:
            point1=line[0]
            point2=line[1]
            cv2.line(img=terrain_image,
                     pt1=point1,
                     pt2=point2,
                     color=1,
                     thickness=5,
                     lineType=cv2.LINE_8)
        #色彩中，0=黑色，1=白色，所以反一下
        # terrain_image=cv2.bitwise_not(terrain_image)
        mask1= terrain_image==1
        mask0= terrain_image==0
        terrain_image[mask1]=0
        terrain_image[mask0]=255

        #cv2和plt上下相反
        # terrain_image=cv2.flip(terrain_image,0)
        self.terrain_image=terrain_image
        self.im=axes.imshow(self.terrain_image,cmap="gray",origin="upper")
        plt.pause(0.01)

        self.ant_series=[self.new_ant()]
        del self.ant_series[0]#只是为了让编译器识别ant类型

        self.history_path=[]
        self.history_best_ant_series=[self.new_ant()]
        del self.history_best_ant_series[0]

        self.history_fail_ant_series=[self.new_ant()]
        del self.history_fail_ant_series[0]

        self.history_update=False

        self.history_max_length=history_max_length

        self.info_density_array=[]
        self.info_density_negative_array=[]


    def InitializeTerrain(self):
        #生成地形(落脚点的可行域)，由几段直线组成
        if self.terrain_type==1:
            #台阶地形
            stair_height=500
            line1=[[0,0],[1780,0]]
            line2=[[1780,stair_height],[3600,stair_height]]
            terrain=[line1,line2]

        x_accuracy=self.accuracy
        terrain_x=[]
        terrain_y=[]
        for i in range(len(terrain)):
            line_point1=terrain[i][0]
            line_point2=terrain[i][1]
            points_number=int(abs(line_point1[0]-line_point2[0])/x_accuracy)

            terrain_x_1=np.linspace(line_point1[0],line_point2[0],points_number)
            terrain_y_1=np.linspace(line_point1[1],line_point2[1],points_number)

            terrain_x=np.concatenate((terrain_x,terrain_x_1))
            terrain_y=np.concatenate((terrain_y,terrain_y_1))

        self.terrain=terrain
        self.terrain_x=terrain_x
        self.terrain_y=terrain_y

    def resetProblem(self):

        return

    def iterate(self):
        #每轮迭代需要生成所有蚂蚁的路径，然后更新信息素
        
        self.iteration_count+=1

        if len(self.ant_series)<self.ant_limit:
            #数量没达到上限之前，每次迭代增加一只蚂蚁
            self.ant_series.append(self.new_ant())
        
        #对所有蚂蚁推进时间
        for i in range(len(self.ant_series)):
            m_ant=self.ant_series[i]
            m_ant.walk(self.info_density_array,
                       self.info_density_negative_array)
            if m_ant.failed:
                #失败的结果也放入一个历史数组
                self.history_fail_ant_series.append(m_ant)
                self.history_update=True
                #失败，重新生成一个替换
                self.ant_series[i]=self.new_ant()

            if m_ant.isEnd():
                #走到终点了，就重新生成一个替换
                self.ant_series[i]=self.new_ant()

                #把结果插入历史队列中(二分法)
                element=m_ant.path_length
                low, high = 0, len(self.history_path)
                while low < high:
                    mid = (low + high) // 2
                    if self.history_path[mid] < element:
                        low = mid + 1
                    else:
                        high = mid
                
                #维护长度
                if low==self.history_max_length:
                    #插入的是最后一个值，丢弃不用
                    pass
                else:
                    self.history_path.insert(low,element)
                    self.history_best_ant_series.insert(low,m_ant)
                    if len(self.history_path)>self.history_max_length:
                        #插入在中间导致了超长，则删除最后一个元素
                        del self.history_path[self.history_max_length]
                        del self.history_best_ant_series[self.history_max_length]
                    self.history_update=True

                    print("current:{0:.2f},best:{1:.2f},bad:{2:.2f},iter:{3}".format(
                        m_ant.path_length,
                        self.history_best_ant_series[0].path_length,
                        self.history_best_ant_series[-1].path_length,
                        self.iteration_count))


            #路径太长，没有继续计算价值，则清除
            min_length=np.inf
            max_length=np.inf
            if len(self.history_best_ant_series)>0:
                min_length=self.history_best_ant_series[0].path_length
                max_length=self.history_best_ant_series[-1].path_length
            case1=m_ant.path_length>min_length*2
            case2=(not len(self.history_best_ant_series)<self.history_max_length)\
                    and (m_ant.path_length>max_length)
            if case1 or case2:
                self.ant_series[i]=self.new_ant()
        
        #迭代完所有蚂蚁以后，更新信息素
        self.updateInfoDensity()

    def updateInfoDensity(self):

        if not self.history_update:
            return
        
        self.info_density_array=[]
        update_len=int(self.history_max_length*0.666)
        update_len=min(len(self.history_best_ant_series),update_len)
        for history_rank in range(update_len):
            #对历史n个最短路径更新信息素
            m_ant=self.history_best_ant_series[history_rank]

            #信息素强度和路径长度在历史路径中的排名有关
            min_x,max_x=0,len(self.history_path)
            min_y,max_y=0,1
            rank=(history_rank-min_x)/(max_x-min_x)
            if (max_x==min_x):
                func_y=max_y
            else:
                func_x=1-rank
                func_y=pow(func_x,10)*(max_y-min_y)+min_y

            path_max_intensity=func_y*100
            if abs(path_max_intensity)<1:continue#过滤一些强度很低的点

            #保存每个路径点(cx,cy,pitch,foot),和对应的信息素强度
            for path_index in range(len(m_ant.path)):
                cx,cy,pitch=m_ant.path[path_index]
                foot=m_ant.foot_path[path_index]

                self.info_density_array.append([cx,cy,pitch,foot,path_max_intensity])
        
        #反作用信息素，对fail蚂蚁处理
        self.info_density_negative_array=[]
        for fail_ant_index in range(len(self.history_fail_ant_series)):
            f_ant=self.history_fail_ant_series[fail_ant_index]
            
            #只考虑末端的几个点
            for path_index in range(len(f_ant.path)):
                cx,cy,pitch=f_ant.path[path_index]
                foot=f_ant.foot_path[path_index]
                
                #越接近失败路径的末端，强度越大
                path_rank=path_index/len(f_ant.path)
                func_x=path_rank
                func_y=pow(func_x,10)
                fail_intensity=func_y*100*(-1)
                if abs(fail_intensity)<1:continue#过滤一些强度很低的点
                self.info_density_negative_array.append([cx,cy,pitch,foot,fail_intensity])

        
        

        self.history_update=False

    def outputSolution(self,text=False):
        #输出
        blue_color=(0,0,255)
        red_color=(255,0,0)
        body_color=red_color
        path_color=blue_color

        plot_body=True
        plot_trajectory=True

        imageArray=np.copy(self.terrain_image)
        imageArray=cv2.cvtColor(imageArray, cv2.COLOR_GRAY2RGB)
        
        # imageArray2=np.copy(self.terrain_image)
        # imageArray2=cv2.cvtColor(imageArray2, cv2.COLOR_GRAY2RGB)

        for m_ant in self.ant_series:
            body_current_pos=m_ant.path[-1]
            foot_current_pos=m_ant.foot_path[-1]
            hip=m_ant.calculate_hip_position()

            #画机身
            body_x,body_y,pitch=body_current_pos
            body1=hip[0]
            body2=hip[3]
            if plot_body:
                cv2.line(img=imageArray,
                        pt1=np.round(body1).astype(int),
                        pt2=np.round(body2).astype(int),
                        color=body_color,
                        thickness=10,
                        lineType=cv2.LINE_8
                        )
                #四个髋关节
                for hip_point in hip:
                    cv2.circle(img=imageArray,
                            center=np.round(hip_point).astype(int),
                            radius=5,
                            color=(255,255,255),
                            thickness=cv2.FILLED)
                #四个脚所在的点
                for foot_point in foot_current_pos:
                    cv2.circle(img=imageArray,
                            center=np.round(foot_point).astype(int),
                            radius=5,
                            color=(255,255,255),
                            thickness=cv2.FILLED)
                #大小腿连杆
                for leg_index in range(4):
                    knee_point=m_ant.calculate_knee_postion(leg_index=leg_index)
                    cv2.circle(img=imageArray,
                            center=np.round(knee_point).astype(int),
                            radius=5,
                            color=(255,255,255),
                            thickness=cv2.FILLED)
                    cv2.line(img=imageArray,
                        pt1=np.round(knee_point).astype(int),
                        pt2=np.round(hip[leg_index]).astype(int),
                        color=body_color,
                        thickness=10,
                        lineType=cv2.LINE_8
                        )
                    cv2.line(img=imageArray,
                        pt1=np.round(knee_point).astype(int),
                        pt2=np.round(foot_current_pos[leg_index]).astype(int),
                        color=body_color,
                        thickness=10,
                        lineType=cv2.LINE_8
                        )
            if plot_trajectory:
                #画轨迹
                for path_index in range(len(m_ant.path)-1):
                    path_point1=m_ant.path[path_index]
                    path_point2=m_ant.path[path_index+1]
                    point1_CoM=[path_point1[0],path_point1[1]]
                    point2_CoM=[path_point2[0],path_point2[1]]
                    cv2.line(img=imageArray,
                        pt1=np.round(point1_CoM).astype(int),
                        pt2=np.round(point2_CoM).astype(int),
                        color=path_color,
                        thickness=3,
                        lineType=cv2.LINE_8
                        )
        
        #画信息素
        imageArray=cv2.cvtColor(imageArray, cv2.COLOR_RGB2RGBA)

        if len(self.info_density_array)==0 and\
            len(self.info_density_negative_array)==0:
            density_array_concat=[]
        elif len(self.info_density_array)==0:
            density_array_concat=self.info_density_negative_array
        elif len(self.info_density_negative_array)==0:
            density_array_concat=self.info_density_array
        else:
            density_array_concat=np.concatenate((self.info_density_array,
                                                self.info_density_negative_array))

        if len(density_array_concat)>0:
            #归一化
            #point=[cx,cy,pitch,[foot],density]
            color_info_density=[point[5-1] for point in density_array_concat]
            color_info_density=np.array(color_info_density)
            #正信息素用蓝色表示，反信息素用红色表示
            mask_positive=color_info_density>0
            mask_negative=color_info_density<0
            max_positive_color=np.max(color_info_density)
            max_negative_color=np.min(color_info_density)
            color_info_density[mask_positive]=color_info_density[mask_positive]/max_positive_color
            color_info_density[mask_negative]=color_info_density[mask_negative]/max_negative_color

            # color_info_density=color_info_density*0.5+0.5 
            color_map = np.zeros((len(density_array_concat), 4))  # 创建颜色数组
            color_map[mask_positive,0:2+1]=blue_color  # 把前三个通道设为颜色
            color_map[mask_negative,0:2+1]=red_color

            color_map[mask_positive,3]=color_info_density[mask_positive]*255  # 将不透明度设为信息素浓度
            color_map[mask_negative,3]=color_info_density[mask_negative]*255  # 将不透明度设为信息素浓度

            for i in range(len(density_array_concat)):
                point=density_array_concat[i]
                cv2.circle(img=imageArray,
                           center=(round(point[0]),round(point[1])),
                           radius=5,
                           color=tuple(color_map[i]),
                           thickness=cv2.FILLED)
        
        self.im.set_array(imageArray)

        return self.im

    def new_ant(self):
        m_ant=ant(self.terrain_x,
                  self.terrain_y,
                  self.accuracy)
        return m_ant
    
def chooseByProb(array,
                 array_prob):
    #输入一个数组，和每项对应的概率
    #返回选择的结果

    sumProb=np.sum(array_prob)
    #生成随机数
    r=random.random()*sumProb
    probCount=0
    for pathIndex in range(len(array)):
        probCount=probCount+array_prob[pathIndex]
        if r<probCount:#随机数落在该概率区间内，说明随机到了这条路径
            return array[pathIndex]
        

alogrithm=None

def figure_update(iteration_count):
    global alogrithm
    alogrithm.iterate()

    plot=True
    if plot:
        alogrithm.outputSolution()

    return alogrithm.im

if __name__=='__main__':

    alogrithm=antAlogorithm(ant_limit=1,
                            accuracy=10)

    ani = FuncAnimation(alogrithm.fig,
                        figure_update,
                        frames=range(1000000),
                        interval=1,
                        blit=False)
    plt.show()